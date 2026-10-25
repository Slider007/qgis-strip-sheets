import os

from qgis.core import Qgis, QgsApplication, QgsVectorLayer
from qgis.PyQt.QtGui import QIcon

from . import altan_toolbar

try:  # Qt6 / QGIS 4: QAction живёт в QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5 / QGIS 3
    from qgis.PyQt.QtWidgets import QAction

PLUGIN_DIR = os.path.dirname(__file__)


class StripSheetsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.provider = None
        self.dialog = None

    def initProcessing(self):
        # QGIS вызывает initProcessing() сам и ещё раз из initGui(): второй провайдер не нужен
        if self.provider is not None:
            return
        from .processing.provider import StripSheetsProvider

        provider = StripSheetsProvider()
        # провайдер с таким id уже есть — QGIS удаляет объект, ссылку держать нельзя
        if QgsApplication.processingRegistry().addProvider(provider):
            self.provider = provider

    def initGui(self):
        self.initProcessing()
        self.action = QAction(QIcon(os.path.join(PLUGIN_DIR, "icon.svg")),
                              "Листы вдоль трассы (атлас и PDF)", self.iface.mainWindow())
        self.action.setToolTip("Разбить линейный объект на листы с рамкой ГОСТ и сохранить их одним PDF")
        self.action.triggered.connect(self.run)
        altan_toolbar.add_action(self.iface, self.action)
        altan_toolbar.add_to_menu(self.iface, self.action)
        self.iface.layoutDesignerOpened.connect(self.designer_opened)

    def designer_opened(self, designer):
        """Альбом модуля открывается сразу с включённым просмотром атласа."""
        from .layout import MARK

        lay = designer.layout()
        if lay is not None and lay.customProperty(MARK) and lay.atlas().enabled():
            designer.setAtlasPreviewEnabled(True)

    def unload(self):
        try:
            self.iface.layoutDesignerOpened.disconnect(self.designer_opened)
        except (TypeError, RuntimeError):   # не подключали (initGui не вызывался)
            pass
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        if self.action:
            altan_toolbar.remove_from_menu(self.iface, self.action)
            altan_toolbar.remove_action(self.iface, self.action)
            self.action.deleteLater()
            self.action = None
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def run(self):
        if self.dialog is None:
            from .dialog import SheetsDialog

            self.dialog = SheetsDialog(self.iface, self.iface.mainWindow())
        layer = self.iface.activeLayer()   # активный линейный слой — сразу в окно
        if isinstance(layer, QgsVectorLayer) and layer.geometryType() == Qgis.GeometryType.Line:
            self.dialog.line.setLayer(layer)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
