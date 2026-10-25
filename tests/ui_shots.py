"""Окно модуля для снимков (scripts/ui_snap.py навыка qgis-plugin): светлая/тёмная тема, крупный шрифт, минимум.

Состояния — как их видит сотрудник: пустой проект и проект с трассой и готовым альбомом.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer  # noqa: E402
from qgis.gui import QgsMapCanvas  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow  # noqa: E402


class Iface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)

    def mainWindow(self): return self.window
    def mapCanvas(self): return self.canvas
    def messageBar(self): return self
    def pushMessage(self, *args): pass


def windows():
    from strip_sheets import layout
    from strip_sheets.dialog import SheetsDialog

    iface = Iface()

    def empty():
        QgsProject.instance().clear()
        return SheetsDialog(iface)

    def with_album():
        project = QgsProject.instance()
        project.clear()
        trace = QgsVectorLayer("LineString?crs=EPSG:32637", "Трасса ВЛ 110 кВ", "memory")
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(500000, 6000000), QgsPointXY(503000, 6000500)]))
        trace.dataProvider().addFeatures([feat])
        project.addMapLayer(trace)
        sheets = QgsVectorLayer("Polygon?crs=EPSG:32637&field=sheet:integer&field=angle:double",
                                "Листы — Трасса ВЛ 110 кВ", "memory")
        sheet = QgsFeature(sheets.fields())
        sheet.setGeometry(QgsGeometry.fromRect(trace.extent().buffered(200)))
        sheet.setAttributes([1, 0.0])
        sheets.dataProvider().addFeatures([sheet])
        project.addMapLayer(sheets)
        layout.build(project, sheets, "A3", 3, 2000, True, {}, sheets.name())
        stamp = {"code": "01-26-ИГДИ", "object": "ВЛ 110 кВ (пример)", "title": "План трассы", "stage": "П",
                 "customer": "Заказчик", "developer": "Иванов", "checker": "Петров", "gip": "Сидоров"}
        for key, value in stamp.items():   # штамп хранится в проекте — окно берёт его оттуда
            project.writeEntry("strip_sheets", key, value)
        dlg = SheetsDialog(iface)
        dlg.line.setLayer(trace)
        dlg.load_albums()
        return dlg

    def rebuild_question():
        dlg = SheetsDialog(iface)
        box = dlg.rebuild_box("Листы — Трасса ВЛ 110 кВ")
        box.owner = dlg   # иначе окно-родитель удалится вместе с вопросом
        return box

    return [("окно_пустой_проект", empty), ("окно_с_альбомом", with_album),
            ("вопрос_собрать_заново", rebuild_question)]
