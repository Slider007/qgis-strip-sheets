import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .sheets_algorithm import SheetsAlgorithm

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))


class StripSheetsProvider(QgsProcessingProvider):
    def id(self):
        return "stripsheets"

    def name(self):
        return "Листы вдоль трассы"

    def icon(self):
        return QIcon(os.path.join(PLUGIN_DIR, "icon.svg"))

    def loadAlgorithms(self):
        self.addAlgorithm(SheetsAlgorithm())
