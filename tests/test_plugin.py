"""Проверки модуля без интерфейса QGIS. Запуск: tests/run_tests.sh

Настройки QGIS уводятся во временный профиль tests/_profile, PDF и снимки листов — в tests/_out.
"""

import datetime
import math
import random
import re
import os
import shutil
import sys
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from qgis.PyQt.QtCore import QCoreApplication, QSettings  # noqa: E402

PROFILE = os.path.join(HERE, "_profile")
OUT = os.path.join(HERE, "_out")
shutil.rmtree(PROFILE, ignore_errors=True)
os.makedirs(OUT, exist_ok=True)
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, PROFILE)
ORG = "strip-sheets-tests"
QCoreApplication.setOrganizationName(ORG)
QCoreApplication.setApplicationName(ORG)

from osgeo import gdal  # noqa: E402
from qgis.core import (  # noqa: E402
    QgsCoordinateReferenceSystem,
    QgsRasterLayer,
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsLayoutExporter,
    QgsLayoutObject,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas  # noqa: E402
from qgis.PyQt import sip  # noqa: E402
from qgis.PyQt.QtCore import QObject, pyqtSignal  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow, QMenu, QToolBar  # noqa: E402

app = QgsApplication([], True, PROFILE)
if os.environ.get("QGIS_PREFIX_PATH"):
    app.setPrefixPath(os.environ["QGIS_PREFIX_PATH"], True)
app.initQgis()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, PROFILE)
assert QSettings().fileName().startswith(PROFILE), QSettings().fileName()

for path in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    plugins = os.path.join(path, "plugins")
    if os.path.isdir(os.path.join(plugins, "processing")) and plugins not in sys.path:
        sys.path.append(plugins)
import processing  # noqa: E402
from processing.core.Processing import Processing  # noqa: E402

Processing.initialize()

with warnings.catch_warnings():
    warnings.simplefilter("error", DeprecationWarning)
    import strip_sheets  # noqa: E402
    from strip_sheets import core, layout  # noqa: E402
    from strip_sheets import dialog as dialog_module  # noqa: E402
    from strip_sheets.processing.provider import StripSheetsProvider  # noqa: E402

PROVIDER = StripSheetsProvider()  # ссылку держать: иначе сборщик мусора удалит алгоритмы
QgsApplication.processingRegistry().addProvider(PROVIDER)
ALG = "stripsheets:sheets"


def line_layer(points, crs="EPSG:32637", name="Трасса"):
    lyr = QgsVectorLayer("LineString?crs=" + crs, name, "memory")
    feat = QgsFeature()
    feat.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in points]))
    lyr.dataProvider().addFeatures([feat])
    return lyr


def run(lyr, **kw):
    params = {"INPUT": lyr, "FORMAT": 1, "MULT": 3, "SCALE": 2000, "ROTATE": True,
              "OVERLAP": 10, "OUTPUT": "memory:"}
    params.update(kw)
    return processing.run(ALG, params)["OUTPUT"]


# синусоида 6 км: трасса с поворотами
CURVE = [(500000 + x, 6000000 + 400 * math.sin(x / 700.0)) for x in range(0, 6001, 50)]


class CoreTest(unittest.TestCase):
    def test_formats(self):
        # ГОСТ 2.301: А3×3 = 420×891, А0×2 = 1189×1682, основные — альбомом
        self.assertEqual(core.page_size("A3", 3), (891, 420))
        self.assertEqual(core.page_size("A0", 2), (1682, 1189))
        self.assertEqual(core.page_size("A4", 1), (297, 210))
        self.assertEqual(core.map_size("A3", 3), (866, 410))

    def test_grid_step(self):
        # 1:2000, 55° с. ш.: 5 см на бумаге = 100 м; 1″ широты ≈ 30,9 м → 5″ (154 м),
        # 1″ долготы ≈ 17,7 м → 10″ (177 м)
        self.assertEqual([round(v * 3600) for v in core.grid_step(2000, 55)], [10, 5])
        # 1:25 000: 1250 м → широта 1′ (1852 м), долгота 2′ (2125 м)
        self.assertEqual([round(v * 3600) for v in core.grid_step(25000, 55)], [120, 60])
        # 1:500: 25 м → широта 1″, долгота 2″
        self.assertEqual([round(v * 3600) for v in core.grid_step(500, 55)], [2, 1])

    def test_straight_line(self):
        # 10 км по оси X, лист 1000×400 м, отступы 0, перекрытие 100 м:
        # 0–1000, 900–1900, …, 8100–9100, 9000–10000 → 11 листов
        line = QgsGeometry.fromPolylineXY([QgsPointXY(0, 0), QgsPointXY(10000, 0)])
        sheets = core.split_line(line, 1000, 400, True, 100)
        self.assertEqual(len(sheets), 11)
        self.assertAlmostEqual(sheets[1][2], 900, delta=0.01)
        self.assertAlmostEqual(sheets[-1][3], 10000, delta=0.01)
        self.assertTrue(all(abs(s[1]) < 1e-9 for s in sheets))
        self.assertAlmostEqual(sheets[0][0].area(), 400000, delta=1)

    def test_diagonal_rotates(self):
        # линия под 45°: с поворотом лист ложится вдоль неё, без поворота листов больше
        line = QgsGeometry.fromPolylineXY([QgsPointXY(0, 0), QgsPointXY(5000, 5000)])
        rotated = core.split_line(line, 1000, 400, True)
        north = core.split_line(line, 1000, 400, False)
        self.assertAlmostEqual(rotated[0][1], 45, delta=1e-6)
        self.assertEqual(len(rotated), math.ceil(5000 * math.sqrt(2) / 1000))
        self.assertGreater(len(north), len(rotated))

    def test_reverse_keeps_north_up(self):
        line = QgsGeometry.fromPolylineXY([QgsPointXY(5000, 100), QgsPointXY(0, 0)])
        sheets = core.split_line(line, 1000, 400, True)
        self.assertTrue(all(-90 <= s[1] <= 90 for s in sheets))

    def test_curve_covered(self):
        line = QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in CURVE])
        for rotate in (True, False):
            sheets = core.split_line(line, 1000, 600, rotate, 20, 30, 80)
            union = QgsGeometry.unaryUnion([s[0] for s in sheets])
            self.assertTrue(union.contains(line), rotate)
            for poly, _a, start, end in sheets:   # каждый участок целиком на своём листе
                part = QgsGeometry(line.constGet().curveSubstring(start, end))
                self.assertTrue(poly.contains(part))


class AlgorithmTest(unittest.TestCase):
    def test_sheets_layer(self):
        out = run(line_layer(CURVE))
        self.assertEqual(out.fields().names(), ["sheet", "angle", "from_m", "to_m"])
        feats = list(out.getFeatures())
        self.assertGreater(len(feats), 2)
        self.assertEqual([f["sheet"] for f in feats], list(range(1, len(feats) + 1)))
        # по умолчанию А3×3 = 891×420, поле карты 866×410 мм, 1:2000 → 1732×820 м
        self.assertAlmostEqual(feats[0].geometry().area(), 1732 * 820, delta=1)

    def test_no_crs_rejected(self):
        with self.assertRaises(Exception) as err:
            run(line_layer([(37, 55), (37.1, 55.1)], ""))
        self.assertIn("не задана система координат", str(err.exception))


class FindingsTest(unittest.TestCase):
    """Находки проверки 25.10: каждый тест падал на прежнем коде."""

    def test_1_loop_trace_stays_whole(self):
        # трасса пересекает сама себя: раньше рвалась в точке пересечения на 3 куска,
        # листы шли не по ходу трассы, отсчёт from_m трижды начинался с нуля
        loop = [(0, 0), (3000, 0), (3000, 1500), (1500, 1500), (1500, -1500), (1500, -3000)]
        out = run(line_layer([(500000 + x, 6000000 + y) for x, y in loop]), MULT=1)
        feats = sorted(out.getFeatures(), key=lambda f: f["sheet"])
        self.assertEqual(feats[0]["from_m"], 0)
        self.assertAlmostEqual(feats[-1]["to_m"], 10500, delta=0.1)   # 3000+1500+1500+3000+1500
        starts = [f["from_m"] for f in feats]
        self.assertEqual(starts, sorted(starts))

    def test_2_theme_not_taken_from_other_album(self):
        # второй альбом за день с темой по умолчанию перезаписывал тему первого
        project = QgsProject.instance()
        project.clear()
        poles = QgsVectorLayer("Point?crs=EPSG:32637", "Опоры", "memory")
        project.addMapLayer(poles)
        sheets_a = run(line_layer(CURVE))
        sheets_a.setName("Листы — А")
        project.addMapLayer(sheets_a)
        theme_a = layout.make_theme(project, "", "Листы — А")
        layout.build(project, sheets_a, "A3", 3, 2000, True, {}, "Листы — А", theme_a)
        project.layerTreeRoot().findLayer(poles.id()).setItemVisibilityChecked(False)
        sheets_b = run(line_layer(CURVE))
        sheets_b.setName("Листы — Б")
        project.addMapLayer(sheets_b)
        theme_b = layout.make_theme(project, "", "Листы — Б")
        self.assertNotEqual(theme_a, theme_b)
        self.assertTrue(theme_b.startswith("Тема оформления 2 от "))
        self.assertIn(poles.id(), project.mapThemeCollection().mapThemeVisibleLayerIds(theme_a))
        # тот же альбом заново — своя тема перезаписывается, номер не растёт
        self.assertEqual(layout.make_theme(project, "", "Листы — А"), theme_a)

    def test_4_degrees_trace_goes_to_project_crs(self):
        # трасса в WGS-84 (KML, GPX): раньше отказ, теперь — в метрической СК проекта
        project = QgsProject.instance()
        project.clear()
        project.setCrs(QgsCoordinateReferenceSystem("EPSG:32637"))
        out = run(line_layer([(39.0, 54.1), (39.05, 54.12)], "EPSG:4326"), MULT=1)
        self.assertEqual(out.crs().authid(), "EPSG:32637")
        self.assertGreater(out.featureCount(), 0)
        self.assertAlmostEqual(sum(f["to_m"] - f["from_m"] for f in out.getFeatures()
                                   if f["sheet"] == out.featureCount()) > 0, True)
        # у проекта тоже градусы — понятный отказ с подсказкой
        project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        with self.assertRaises(Exception) as err:
            run(line_layer([(39.0, 54.1), (39.05, 54.12)], "EPSG:4326"))
        self.assertIn("Проект → Свойства", str(err.exception))


class SmallFindingsTest(unittest.TestCase):
    """Мелкие находки проверки 25.10 (5, 6, 8, 10): каждый тест падал на прежнем коде."""

    def test_5_overlap_capped(self):
        # вертикальная трасса 3 км, А4 без поворота: каждый лист охватывает 160 м трассы.
        # Перекрытие 200 мм (400 м) больше охвата — раньше листы сдвигались на половину: 37 листов
        vertical = line_layer([(500000, 6000000), (500000, 6003000)])
        out = run(vertical, FORMAT=0, MULT=1, ROTATE=False, OVERLAP=200)
        self.assertLessEqual(out.featureCount(), 26)   # сдвиг не меньше ¾ охвата: 3000 / 120 + 1

    def test_8_metres_run_through_all_lines(self):
        # две выделенные линии по 3 км: раньше вторая снова считалась от нуля
        lyr = line_layer([(500000, 6000000), (503000, 6000000)])
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(500000, 6005000), QgsPointXY(503000, 6005000)]))
        lyr.dataProvider().addFeatures([feat])
        feats = sorted(run(lyr, MULT=1).getFeatures(), key=lambda f: f["sheet"])
        starts = [f["from_m"] for f in feats]
        self.assertEqual(starts, sorted(starts))
        self.assertAlmostEqual(feats[-1]["to_m"], 6000, delta=0.1)

    def test_6_write_error_is_named(self):
        # папка проекта только для чтения: раньше None — и окно писало «Проект не сохранён»
        project = QgsProject.instance()
        project.clear()
        folder = os.path.join(OUT, "только_чтение")
        os.makedirs(folder, exist_ok=True)
        os.chmod(folder, 0o755)
        project.write(os.path.join(folder, "проект.qgz"))
        os.chmod(folder, 0o555)
        try:
            saved, error = layout.save_sheets(project, run(line_layer(CURVE)), "Листы — Т")
        finally:
            os.chmod(folder, 0o755)
        self.assertIsNone(saved)
        self.assertIn("проект_листы.gpkg", error)
        project.clear()
        self.assertEqual(layout.save_sheets(project, run(line_layer(CURVE)), "Листы — Т"), (None, ""))

    def test_10_foreign_layout_name_keeps_meaning(self):
        # чужой макет «Листы — Т»: раньше альбом получал безликое «Макет 1»
        project = QgsProject.instance()
        project.clear()
        foreign = QgsPrintLayout(project)
        foreign.setName("Листы — Т")
        project.layoutManager().addLayout(foreign)
        sheets = run(line_layer(CURVE))
        project.addMapLayer(sheets)
        lay = layout.build(project, sheets, "A3", 3, 2000, True, {}, "Листы — Т")
        self.assertEqual(lay.name(), "Листы — Т (2)")
        self.assertIs(project.layoutManager().layoutByName("Листы — Т"), foreign)


class LayoutTest(unittest.TestCase):
    def test_atlas_pdf(self):
        project = QgsProject.instance()
        project.clear()
        trace = line_layer(CURVE)
        trace.renderer().symbol().setWidth(1.5)
        project.addMapLayer(trace)
        sheets = run(trace, FORMAT=1, MULT=3, SCALE=2000)
        sheets.setName("Листы")
        project.addMapLayer(sheets)
        layout.style_sheets(sheets)
        stamp = {"code": "123-ИЭИ", "object": "Трасса", "developer": "Иванов"}
        lay = layout.build(project, sheets, "A3", 3, 2000, True, stamp, "Листы — тест")
        page = lay.pageCollection().page(0).pageSize()
        self.assertEqual((page.width(), page.height()), (891, 420))
        maps = [i for i in lay.items() if type(i).__name__ == "QgsLayoutItemMap"]
        self.assertEqual(len(maps), 2)
        # в сцене одна страница: лишняя страница по умолчанию роняла QGIS при выводе альбома
        # с подложкой из плиток (воспроизведено медленным сервером плиток на 3.40, 3.44 и 4.2.3)
        self.assertEqual(len([i for i in lay.items() if type(i).__name__ == "QgsLayoutItemPage"]), 1)
        # условные обозначения — из слоёв проекта: трасса есть, слоя листов нет; в шаблонах
        # чужих слоёв (и названий объектов) не осталось
        for legend in [i for i in lay.items() if type(i).__name__ == "QgsLayoutItemLegend"]:
            self.assertEqual([n.name() for n in legend.model().rootGroup().findLayers()], ["Трасса"])
        # подпись «Лист N» не стоит посреди своего же листа
        self.assertIn("@atlas_featureid", sheets.labeling().settings().fieldName)
        # одна стрелка севера на все листы, крутится вместе с картой
        arrows = [i for i in lay.items() if type(i).__name__ == "QgsLayoutItemPicture"]
        self.assertEqual(len(arrows), 1)
        self.assertIsNotNone(arrows[0].linkedMap())
        # и её не закрывают условные обозначения ни одной из форм
        for legend in [i for i in lay.items() if type(i).__name__ == "QgsLayoutItemLegend"]:
            self.assertFalse(arrows[0].sceneBoundingRect().intersects(legend.sceneBoundingRect()))
        self.assertFalse(arrows[0].dataDefinedProperties().isActive(
            QgsLayoutObject.DataDefinedProperty.ExcludeFromExports))
        # трасса около 54° с. ш., 1:2000 → сетка 10″ × 5″ на обеих формах
        for m in maps:
            grid = m.grids().asList()[0]
            self.assertEqual((round(grid.intervalX() * 3600), round(grid.intervalY() * 3600)), (10, 5))

        # на каждом листе карта показывает ровно свой прямоугольник (проверка поворота)
        atlas = lay.atlas()
        atlas.beginRender()
        count = atlas.count()
        self.assertEqual(count, sheets.featureCount())
        for n in range(count):
            atlas.seekTo(n)
            feat = atlas.layout().reportContext().feature()
            for m in maps:
                seen = QgsGeometry.fromQPolygonF(m.visibleExtentPolygon())
                diff = seen.symDifference(feat.geometry()).area()
                self.assertLess(diff, feat.geometry().area() * 1e-4, (n, m.id()))
        atlas.endRender()

        # пересоздание того же макета не плодит копий
        layout.build(project, sheets, "A3", 3, 2000, True, stamp, "Листы — тест")
        self.assertEqual(len(project.layoutManager().printLayouts()), 1)
        lay = project.layoutManager().layoutByName("Листы — тест")

        pdf = os.path.join(OUT, "листы.pdf")
        self.assertEqual(layout.export_pdf(lay, pdf), "")
        with open(pdf, "rb") as f:
            pages = re.findall(rb"/Count (\d+)", f.read())
        self.assertEqual(int(pages[0]), count)
        # снимки первых двух листов — глазами проверить рамку
        settings = QgsLayoutExporter.ImageExportSettings()
        settings.dpi = 60
        QgsLayoutExporter.exportToImage(lay.atlas(), os.path.join(OUT, "лист"), "png", settings)
        # растровая подложка: при 150 dpi файл заметно легче, чем при 300
        tif = os.path.join(OUT, "подложка.tif")
        drv = gdal.GetDriverByName("GTiff").Create(tif, 1500, 300, 3, gdal.GDT_Byte)
        drv.SetGeoTransform((499800, 5, 0, 6001000, 0, -7))
        drv.SetProjection(QgsCoordinateReferenceSystem("EPSG:32637").toWkt())
        rnd = random.Random(1)
        for b in (1, 2, 3):
            drv.GetRasterBand(b).WriteRaster(0, 0, 1500, 300, bytes(rnd.randrange(256) for _ in range(450000)))
        drv = None
        project.addMapLayer(QgsRasterLayer(tif, "Подложка"))
        sizes = {}
        for dpi in (150, 300):
            path = os.path.join(OUT, "листы_{}.pdf".format(dpi))
            self.assertEqual(layout.export_pdf(lay, path, dpi), "")
            sizes[dpi] = os.path.getsize(path)
        print("PDF, байт:", sizes)
        self.assertLess(sizes[150] * 2, sizes[300])


# ------------------------------------------------------------ модуль в QGIS

class MessageBar:
    def __init__(self):
        self.messages = []

    def pushMessage(self, title, text, level, duration):
        self.messages.append((text, level))


class Iface(QObject):
    layoutDesignerOpened = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.bar = MessageBar()
        self.menu = []
        self.plugin_menu = QMenu("Модули")
        self.layer = None
        self.opened = []

    def mainWindow(self): return self.window
    def mapCanvas(self): return self.canvas
    def messageBar(self): return self.bar
    def activeLayer(self): return self.layer
    def openLayoutDesigner(self, lay): self.opened.append(lay)
    def addToolBar(self, name):
        bar = QToolBar(name)
        self.window.addToolBar(bar)
        sip.transferback(bar)
        return bar
    def pluginMenu(self): return self.plugin_menu
    def addPluginToMenu(self, m, a): self.menu.append((m, a.text()))
    def removePluginMenu(self, m, a): self.menu.remove((m, a.text()))


class Designer:
    def __init__(self, lay):
        self.lay = lay
        self.preview = False

    def layout(self): return self.lay
    def setAtlasPreviewEnabled(self, on): self.preview = on


class ButtonTest(unittest.TestCase):
    def test_dialog_flow(self):
        project = QgsProject.instance()
        project.clear()
        iface = Iface()
        plugin = strip_sheets.classFactory(iface)
        plugin.initProcessing()   # провайдер уже есть (PROVIDER) — второй отвергается
        plugin.initGui()
        self.assertEqual(len(iface.menu), 1)

        trace = line_layer(CURVE)
        project.addMapLayer(trace)
        project.write(os.path.join(OUT, "проект.qgz"))   # сохранённый проект: листы пойдут в GeoPackage
        iface.layer = trace
        plugin.run()
        dlg = plugin.dialog
        self.assertIs(dlg.line.currentLayer(), trace)
        self.assertFalse(dlg.pdf_btn.isEnabled())
        dlg.split()   # ничего не выделено — подсказка, листов нет
        self.assertIn("ничего не выделено", iface.bar.messages[-1][0])
        trace.selectAll()
        dlg.stamp["code"].setText("77-ИЭИ")
        dlg.confirm_rebuild = lambda name: True
        dlg.split()
        self.assertIn("Готово:", iface.bar.messages[-1][0])
        # тема без названия — «Тема оформления 1» с датой, карты альбома идут по ней
        theme = "Тема оформления 1 от " + datetime.date.today().strftime("%d.%m.%Y")
        self.assertIn(theme, iface.bar.messages[-1][0])
        self.assertTrue(project.mapThemeCollection().hasMapTheme(theme))
        maps = [i for i in dlg.current_layout().items() if type(i).__name__ == "QgsLayoutItemMap"]
        self.assertEqual({(m.followVisibilityPreset(), m.followVisibilityPresetName()) for m in maps},
                         {(True, theme)})
        sheets = project.mapLayersByName("Листы — Трасса")
        self.assertEqual(len(sheets), 1)
        self.assertIn("проект_листы.gpkg", sheets[0].source())
        self.assertEqual(project.readEntry("strip_sheets", "code")[0], "77-ИЭИ")
        # находка 9: альбом уже есть — сначала вопрос; «Отмена» ничего не меняет
        lay_before = dlg.current_layout()
        asked = []
        dlg.confirm_rebuild = lambda name: asked.append(name) or False
        dlg.split()
        self.assertEqual(asked, ["Листы — Трасса"])
        self.assertIs(dlg.current_layout(), lay_before)
        dlg.confirm_rebuild = lambda name: True
        self.assertIn("Ручные правки", dlg.rebuild_box("Листы — Трасса").informativeText())
        dlg.theme.setText("Вариант для заказчика")
        dlg.split()   # повтор заменяет слой и макет, а не плодит копии; тема — своя
        self.assertEqual(len(project.mapLayersByName("Листы — Трасса")), 1)
        self.assertEqual(len(project.layoutManager().printLayouts()), 1)
        self.assertTrue(dlg.pdf_btn.isEnabled())
        self.assertEqual(dlg.album.currentText(), "Листы — Трасса")
        self.assertTrue(project.mapThemeCollection().hasMapTheme(
            "Вариант для заказчика от " + datetime.date.today().strftime("%d.%m.%Y")))

        # штамп меняется в готовом альбоме, ручная правка в макете остаётся
        lay = dlg.current_layout()
        lay.setCustomProperty("моя правка", 1)
        dlg.stamp["code"].setText("78-ИЭИ")
        dlg.refresh_stamp()
        codes = {i.text() for i in lay.items() if getattr(i, "id", lambda: "")() == "1-обозначение документа"}
        self.assertEqual(codes, {"78-ИЭИ"})
        self.assertIs(dlg.current_layout(), lay)
        self.assertEqual(lay.customProperty("моя правка"), 1)
        # в форме 6 над графами «Изм.» ещё две строки
        self.assertEqual(len([i for i in lay.items() if getattr(i, "id", lambda: "")().endswith("-верх")]), 7)

        # находка 3: набранный штамп не теряется, если закрыть окно без разбивки
        dlg.stamp["gip"].setText("Петров")
        dlg.hide()
        dlg.show()
        self.assertEqual(dlg.stamp["gip"].text(), "Петров")
        dlg.hide()

        # макет альбома открывается с включённым атласом, чужой — как был
        designer = Designer(lay)
        iface.layoutDesignerOpened.emit(designer)
        self.assertTrue(designer.preview)
        other = Designer(QgsPrintLayout(project))
        iface.layoutDesignerOpened.emit(other)
        self.assertFalse(other.preview)

        pdf = os.path.join(OUT, "окно.pdf")
        dialog_module.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (pdf[:-4], ""))
        dialog_module.QDesktopServices.openUrl = staticmethod(lambda url: True)
        dlg.save_pdf()
        self.assertTrue(os.path.exists(pdf), iface.bar.messages[-1])
        self.assertIn("МБ", iface.bar.messages[-1][0])

        # после повторного открытия проекта альбом и листы на месте
        project.write()
        project.clear()
        self.assertTrue(project.read(os.path.join(OUT, "проект.qgz")))
        self.assertEqual(layout.albums(project), ["Листы — Трасса"])
        reopened = project.layoutManager().layoutByName("Листы — Трасса")
        self.assertTrue(reopened.atlas().coverageLayer().isValid())
        self.assertGreater(reopened.atlas().coverageLayer().featureCount(), 2)
        dlg.show()
        self.assertEqual(dlg.stamp["code"].text(), "78-ИЭИ")
        self.assertEqual(dlg.album.currentText(), "Листы — Трасса")
        dlg.hide()

        plugin.unload()
        self.assertEqual(iface.menu, [])


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    app.exitQgis()
    sys.exit(0 if result.wasSuccessful() else 1)
