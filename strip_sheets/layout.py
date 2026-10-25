"""Макет-атлас с рамкой ГОСТ: первый лист — форма 3, остальные — форма 6; выгрузка в один PDF."""
import datetime
import os

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutObject,
    QgsLayoutPoint,
    QgsLayoutRenderContext,
    QgsLayerTreeModel,
    QgsLayoutSize,
    QgsMapThemeCollection,
    QgsPalLayerSettings,
    QgsPrintLayout,
    QgsProperty,
    QgsReadWriteContext,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsExpressionContextUtils,
    QgsFillSymbol,
)
from qgis.PyQt.QtXml import QDomDocument

from . import core

TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")
MARK = "strip_sheets"   # свойство макета: его создал этот модуль и его можно пересоздать
FIRST, OTHERS = "@atlas_featurenumber = 1", "@atlas_featurenumber > 1"

# поля штампа: ключ настройки → id надписи в шаблонах
STAMP = {
    "code": "1-обозначение документа",
    "object": "2-наименование комплекса",
    "title": "3-наименование здания",
    "stage": "6-обозначение стадии",
    "customer": "27-ЗАКАЗЧИК",
}


def _template(name):
    doc = QDomDocument()
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
        doc.setContent(f.read())
    return doc


def _show_only(items, condition):
    """Надписи одной формы видны только на своих листах (и в окне макета, и в PDF)."""
    for item in items:
        if isinstance(item, QgsLayoutItemPage):   # шаблон отдаёт и страницу: её не прятать
            continue
        props = item.dataDefinedProperties()
        props.setProperty(QgsLayoutObject.DataDefinedProperty.ExcludeFromExports,
                          QgsProperty.fromExpression("NOT ({})".format(condition)))
        props.setProperty(QgsLayoutObject.DataDefinedProperty.Opacity,
                          QgsProperty.fromExpression("if({}, 100, 0)".format(condition)))
        item.setDataDefinedProperties(props)


def _setup_map(layout, items, crs, scale, rotate, w, h, lat):
    maps = [i for i in items if isinstance(i, QgsLayoutItemMap)]
    step_x, step_y = core.grid_step(scale, lat)
    for m in maps:
        for grid in m.grids().asList():   # географическая сетка шаблона — круглым шагом под масштаб
            if grid.crs().isGeographic():
                grid.setUnits(QgsLayoutItemMapGrid.GridUnit.MapUnit)
                grid.setIntervalX(step_x)
                grid.setIntervalY(step_y)
                grid.setOffsetX(0)
                grid.setOffsetY(0)
                # на повёрнутом листе линии обоих направлений выходят на все стороны и подписи слипаются:
                # долгота — только сверху и снизу, широта — только слева и справа
                side, show = QgsLayoutItemMapGrid.BorderSide, QgsLayoutItemMapGrid.DisplayMode
                for s in (side.Top, side.Bottom):
                    grid.setAnnotationDisplay(show.LongitudeOnly, s)
                for s in (side.Left, side.Right):
                    grid.setAnnotationDisplay(show.LatitudeOnly, s)
        m.attemptMove(QgsLayoutPoint(core.FRAME_LEFT, core.FRAME))
        m.attemptResize(QgsLayoutSize(w - core.FRAME_LEFT - core.FRAME, h - 2 * core.FRAME))
        m.setCrs(crs)
        m.setKeepLayerSet(False)
        m.setFollowVisibilityPreset(False)
        m.setAtlasDriven(True)
        m.setAtlasScalingMode(QgsLayoutItemMap.AtlasScalingMode.Fixed)
        m.setScale(scale)
        if rotate:
            props = m.dataDefinedProperties()
            props.setProperty(QgsLayoutObject.DataDefinedProperty.MapRotation,
                              QgsProperty.fromField("angle"))
            m.setDataDefinedProperties(props)
        layout.moveItemToBottom(m)   # карта под рамкой и штампом
    return maps[0] if maps else None


def _set_text(items, key, text):
    for item in items:
        if isinstance(item, QgsLayoutItemLabel) and item.id() == key:
            item.setText(text)


def _theme_users(project, title):
    """Названия макетов, карты которых рисуются по теме title."""
    return {lay.name() for lay in project.layoutManager().printLayouts()
            for m in lay.items() if isinstance(m, QgsLayoutItemMap)
            and m.followVisibilityPreset() and m.followVisibilityPresetName() == title}


def make_theme(project, name, album):
    """Тема оформления для альбома album из того, что сейчас видно на карте (слои и их стили),
    с датой в названии. Свою прежнюю тему альбом перезаписывает; тему другого альбома не трогает —
    берётся следующий номер («Тема оформления 2 от …», «Вариант (2) от …»). Возвращает название."""
    date = datetime.date.today().strftime("%d.%m.%Y")
    number = 1
    while True:
        if name:
            label = name if number == 1 else "{} ({})".format(name, number)
        else:
            label = "Тема оформления {}".format(number)
        title = "{} от {}".format(label, date)
        if not _theme_users(project, title) - {album}:
            break
        number += 1
    root = project.layerTreeRoot()
    model = QgsLayerTreeModel(root)
    themes = project.mapThemeCollection()
    record = QgsMapThemeCollection.createThemeFromCurrentState(root, model)
    if themes.hasMapTheme(title):
        themes.update(title, record)
    else:
        themes.insert(title, record)
    return title


def build(project, sheets, fmt, mult, scale, rotate, stamp, name, theme=None):
    """Создать (или пересоздать свой) макет-атлас по слою листов. Возвращает макет.
    theme — тема оформления, по которой рисуются карты альбома."""
    manager = project.layoutManager()
    old = manager.layoutByName(name)
    if old is not None:
        if old.customProperty(MARK):
            manager.removeLayout(old)
        else:   # чужой макет с таким именем не трогаем: «Листы — X (2)», «(3)»…
            base, number = name, 2
            while manager.layoutByName(name) is not None:
                name = "{} ({})".format(base, number)
                number += 1
    # без initializeDefaults(): шаблон приносит свою страницу, а страница по умолчанию оставалась в сцене
    # до отложенного удаления и роняла QGIS, если оно срабатывало посреди вывода (подложка из плиток
    # OpenStreetMap или космоснимков — QGIS ждёт плитки и обрабатывает события): 3.40, 3.44 и 4.2.3
    layout = QgsPrintLayout(project)
    context = QgsReadWriteContext()
    first, _ = layout.loadFromTemplate(_template("forma_3.qpt"), context)
    doc = _template("forma_6.qpt")
    others = layout.addItemsFromXml(doc.documentElement(), doc, context)
    layout.setName(name)
    layout.setCustomProperty(MARK, True)
    layout.renderContext().setDpi(150)   # в шаблонах 300: файл выходит тяжёлым
    QgsExpressionContextUtils.setLayoutVariable(layout, "layout_tip", 0)  # переменная шаблонов

    w, h = core.page_size(fmt, mult)
    layout.pageCollection().page(0).setPageSize(QgsLayoutSize(w, h))
    to_wgs = QgsCoordinateTransform(sheets.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), project)
    lat = to_wgs.transformBoundingBox(sheets.extent()).center().y()
    map1 = _setup_map(layout, first, sheets.crs(), scale, rotate, w, h, lat)
    map2 = _setup_map(layout, others, sheets.crs(), scale, rotate, w, h, lat)
    if theme:   # альбом не меняется, когда на основной карте включают и выключают слои
        for m in (map1, map2):
            m.setFollowVisibilityPreset(True)
            m.setFollowVisibilityPresetName(theme)
    # указатель севера из формы 3 — один на все листы, крутится вместе с картой (у обеих карт поворот
    # из одного поля)
    for item in [i for i in first if isinstance(i, QgsLayoutItemPicture)]:
        item.setLinkedMap(map1)
        first.remove(item)   # не прятать на листах формы 6
        # правый верхний угол поля карты: слева у формы 6 стоят условные обозначения и закрывали бы стрелку
        size = item.sizeWithUnits()
        item.attemptMove(QgsLayoutPoint(w - core.FRAME - 8 - size.width(), core.FRAME + 12))

    every = first + others
    for item in every:   # условные обозначения — из слоёв проекта, без листов и растровой подложки
        if isinstance(item, QgsLayoutItemLegend):
            item.setAutoUpdateModel(True)    # дерево слоёв проекта вместо списка из шаблона
            item.setAutoUpdateModel(False)   # своя копия: из неё убираем лишнее, проект не трогаем
            for node in item.model().rootGroup().findLayers():
                if node.layer() is sheets or not isinstance(node.layer(), QgsVectorLayer):
                    node.parent().removeChildNode(node)
    _set_text(every, "7-номер листа", "[%@atlas_featurenumber%]")
    _set_text(every, "8-кол-во листов", "[%@atlas_totalfeatures%]")
    _set_text(every, "4-наимнование изображений", "Масштаб 1:{:,}".format(scale).replace(",", " "))
    update_stamp(layout, stamp)
    _show_only(first, FIRST)
    _show_only(others, OTHERS)

    atlas = layout.atlas()
    atlas.setCoverageLayer(sheets)
    atlas.setHideCoverage(True)
    atlas.setSortFeatures(True)
    atlas.setSortExpression('"sheet"')
    atlas.setFilenameExpression("'лист_' || @atlas_featurenumber")
    atlas.setEnabled(True)
    manager.addLayout(layout)
    return layout


def albums(project):
    """Макеты, созданные модулем, — по имени."""
    return sorted(lay.name() for lay in project.layoutManager().printLayouts() if lay.customProperty(MARK))


def update_stamp(layout, stamp):
    """Переписать графы штампа в готовом макете; остальное в макете не трогается."""
    items = layout.items()
    for key, label in STAMP.items():
        _set_text(items, label, stamp.get(key, ""))
    _set_text(items, "11-ФИО", "{}\n{}\n\n\n{}\n".format(
        stamp.get("developer", ""), stamp.get("checker", ""), stamp.get("gip", "")))
    layout.refresh()


def export_pdf(layout, path, dpi=150):
    """Все листы атласа одним PDF. Возвращает текст ошибки или ''.
    Лёгкий файл: растр (подложка) в dpi и со сжатием с потерями, линии упрощаются до точности печати."""
    context = layout.renderContext()
    context.setDpi(dpi)
    try:
        lossless = Qgis.LayoutRenderFlag.LosslessImageRendering
    except AttributeError:   # QGIS 3.40
        lossless = QgsLayoutRenderContext.FlagLosslessImageRendering
    context.setFlag(lossless, False)
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = dpi
    settings.simplifyGeometries = True
    settings.rasterizeWholeImage = False
    result, error = QgsLayoutExporter.exportToPdf(layout.atlas(), path, settings)
    if result != QgsLayoutExporter.ExportResult.Success:
        return error or "код {}".format(result)
    return ""


def save_sheets(project, layer, name):
    """Записать листы в GeoPackage рядом с проектом (временный слой не переживает закрытие проекта).
    Возвращает (слой из файла, '') или (None, '') — проект ещё не сохранён — или (None, путь) — записать не вышло."""
    if not project.fileName():
        return None, ""
    path = os.path.join(project.absolutePath(), project.baseName() + "_листы.gpkg")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = name
    options.actionOnExistingFile = (QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
                                    if os.path.exists(path) else
                                    QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile)
    error = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, project.transformContext(), options)
    if error[0] != QgsVectorFileWriter.WriterError.NoError:
        return None, path
    saved = QgsVectorLayer("{}|layername={}".format(path, name), name, "ogr")
    return (saved, "") if saved.isValid() else (None, path)


def style_sheets(layer):
    """Слой листов: красный контур без заливки и подпись «Лист N»."""
    layer.renderer().setSymbol(QgsFillSymbol.createSimple(
        {"color": "0,0,0,0", "outline_color": "214,39,40", "outline_width": "0.6"}))
    settings = QgsPalLayerSettings()
    # на своём листе альбома подпись не нужна: контур скрыт, а надпись стояла бы посреди карты
    settings.fieldName = "if(@atlas_featureid = $id, '', 'Лист ' || \"sheet\")"
    settings.isExpression = True
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.triggerRepaint()
