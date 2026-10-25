from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
)
from qgis.PyQt.QtCore import QCoreApplication, QMetaType

from .. import core

FORMATS = list(core.BASE)


def sheet_fields():
    fields = QgsFields()
    for name, kind in (("sheet", QMetaType.Type.Int), ("angle", QMetaType.Type.Double),
                       ("from_m", QMetaType.Type.Double), ("to_m", QMetaType.Type.Double)):
        field = QgsField(name, kind)
        if kind == QMetaType.Type.Double:
            field.setPrecision(1 if name != "angle" else 2)
        fields.append(field)
    return fields


class SheetsAlgorithm(QgsProcessingAlgorithm):
    INPUT, FORMAT, MULT, SCALE, ROTATE, OVERLAP, OUTPUT = (
        "INPUT", "FORMAT", "MULT", "SCALE", "ROTATE", "OVERLAP", "OUTPUT")

    def tr(self, text):
        return QCoreApplication.translate("SheetsAlgorithm", text)

    def createInstance(self):
        return SheetsAlgorithm()

    def name(self):
        return "sheets"

    def displayName(self):
        return self.tr("Листы вдоль трассы")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def shortHelpString(self):
        return self.tr(
            "Разбивает линию (трассу) на листы заданного формата и масштаба. Листы идут от начала линии "
            "к концу, соседние заходят друг на друга на величину перекрытия. Поле sheet — номер листа, "
            "angle — поворот карты на листе, from_m и to_m — участок трассы на листе, м от начала (при нескольких "
            "линиях — сквозной счёт). Перекрытие больше четверти листа не делается. "
            "Если выделены объекты, берутся только они. Трасса в градусах (KML, GPX) пересчитывается в систему "
            "координат проекта — она должна быть метрической (МСК, UTM).")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Линия (трасса)"), [Qgis.ProcessingSourceType.VectorLine]))
        self.addParameter(QgsProcessingParameterEnum(
            self.FORMAT, self.tr("Формат"), FORMATS, defaultValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.MULT, self.tr("Кратность (А3×3 — кратность 3, 1 — основной формат)"),
            Qgis.ProcessingNumberParameterType.Integer, defaultValue=3, minValue=1, maxValue=9))
        self.addParameter(QgsProcessingParameterNumber(
            self.SCALE, self.tr("Масштаб 1:"), Qgis.ProcessingNumberParameterType.Integer,
            defaultValue=2000, minValue=1))
        self.addParameter(QgsProcessingParameterBoolean(
            self.ROTATE, self.tr("Поворачивать листы по трассе"), defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.OVERLAP, self.tr("Перекрытие листов, мм на бумаге (не больше четверти листа)"),
            Qgis.ProcessingNumberParameterType.Double,
            defaultValue=10, minValue=0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Листы"), Qgis.ProcessingSourceType.VectorPolygon))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))
        crs = source.sourceCrs()
        if not crs.isValid():
            raise QgsProcessingException(self.tr(
                "У слоя трассы не задана система координат. Задайте её: «Свойства слоя → Источник», "
                "затем повторите."))
        target, transform = crs, None
        if crs.mapUnits() != Qgis.DistanceUnit.Meters:   # KML, GPX: листы — в метрической СК проекта
            project = context.project()
            target = project.crs() if project is not None else QgsCoordinateReferenceSystem()
            if not target.isValid() or target.mapUnits() != Qgis.DistanceUnit.Meters:
                raise QgsProcessingException(self.tr(
                    "Трасса в градусах ({}), а у проекта нет метрической системы координат. Задайте проекту "
                    "МСК или UTM («Проект → Свойства → Система координат») и повторите.").format(crs.authid()))
            transform = QgsCoordinateTransform(crs, target, context.transformContext())
            feedback.pushInfo(self.tr("Трасса пересчитана из {} в {}.").format(crs.authid(), target.authid()))
        fmt = FORMATS[self.parameterAsEnum(parameters, self.FORMAT, context)]
        mult = self.parameterAsInt(parameters, self.MULT, context)
        scale = self.parameterAsInt(parameters, self.SCALE, context)
        rotate = self.parameterAsBool(parameters, self.ROTATE, context)
        k = scale / 1000.0  # мм на бумаге → м на местности
        overlap = self.parameterAsDouble(parameters, self.OVERLAP, context) * k

        geoms = []
        for f in source.getFeatures():
            if f.hasGeometry() and not f.geometry().isEmpty():
                geom = QgsGeometry(f.geometry())
                if transform is not None:
                    geom.transform(transform)
                geoms.append(geom)
        if not geoms:
            raise QgsProcessingException(self.tr(
                "На слое нет линий для разбивки. Выберите слой трассы или выделите на нём линию."))
        # сливаются только куски, стыкующиеся концами; unaryUnion не годится — он режет трассу
        # в местах самопересечения, и листы идут не по ходу трассы
        lines = QgsGeometry.collectGeometry(geoms).mergeLines()

        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, sheet_fields(),
                                          Qgis.WkbType.Polygon, target)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))
        map_w, map_h = core.map_size(fmt, mult)
        number = 0
        parts = lines.asGeometryCollection() if lines.isMultipart() else [lines]
        passed = 0.0   # метры от начала идут сквозным счётом через все линии
        for i, part in enumerate(parts):
            if feedback.isCanceled():
                break
            sheets = core.split_line(part, map_w * k, map_h * k, rotate, overlap,
                                     core.PAD_X * k, core.PAD_Y * k)
            for poly, angle, start, end in sheets:
                number += 1
                feat = QgsFeature(sheet_fields())
                feat.setGeometry(poly)
                feat.setAttributes([number, round(angle, 2), round(passed + start, 1), round(passed + end, 1)])
                sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            passed += part.length()
            feedback.setProgress(100.0 * (i + 1) / len(parts))
        feedback.pushInfo(self.tr("Листов: {}").format(number))
        return {self.OUTPUT: dest}
