import os

from qgis.core import Qgis, QgsProcessingFeatureSourceDefinition, QgsProject, QgsSettings
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from . import core, layout

TITLE = "Листы вдоль трассы"
SCALES = ["500", "1000", "2000", "5000", "10000", "25000", "50000", "100000"]
STAMP_FIELDS = [("code", "Обозначение"), ("object", "Объект"), ("title", "Наименование"),
                ("stage", "Стадия"), ("customer", "Заказчик"),
                ("developer", "Разработал"), ("checker", "Проверил"), ("gip", "ГИП")]
SCOPE = "strip_sheets"


class SheetsDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle(TITLE)
        settings = QgsSettings()

        self.line = QgsMapLayerComboBox()
        self.line.setFilters(Qgis.LayerFilters(Qgis.LayerFilter.LineLayer))
        self.selected = QCheckBox("Только выделенные объекты")
        self.selected.setChecked(True)
        self.fmt = QComboBox()
        self.fmt.addItems([f.replace("A", "А") for f in core.BASE])
        self.fmt.setCurrentIndex(settings.value(SCOPE + "/format", 1, int))
        self.mult = QSpinBox()
        self.mult.setRange(1, 9)
        self.mult.setPrefix("× ")
        self.mult.setValue(settings.value(SCOPE + "/mult", 3, int))
        self.size = QLabel()
        self.scale = QComboBox()
        self.scale.setEditable(True)
        self.scale.addItems(SCALES)
        self.scale.setCurrentText(settings.value(SCOPE + "/scale", "2000"))
        self.rotate = QCheckBox("Поворачивать листы по трассе (иначе север строго вверх)")
        self.rotate.setChecked(settings.value(SCOPE + "/rotate", True, bool))
        self.overlap = QSpinBox()
        self.overlap.setRange(0, 200)
        self.overlap.setSuffix(" мм")
        self.overlap.setToolTip("На сколько соседние листы заходят друг на друга, мм на бумаге. "
                                "Больше четверти листа модуль не делает")
        self.overlap.setValue(settings.value(SCOPE + "/overlap", 10, int))

        self.dpi = QComboBox()
        self.dpi.addItems(["100", "150", "200", "300"])
        self.dpi.setCurrentText(settings.value(SCOPE + "/dpi", "150"))
        self.dpi.setToolTip("Чем меньше, тем легче PDF. 150 — для печати чертежей достаточно, "
                            "300 — если на подложке мелкий текст")

        self.theme = QLineEdit()
        self.theme.setPlaceholderText("Тема оформления 1")
        self.theme.setToolTip("Что сейчас видно на карте (слои и их стили) запоминается как тема с этим названием "
                              "и сегодняшней датой; карты альбома рисуются по ней")

        fmt_row = QHBoxLayout()
        for w in (self.fmt, self.mult, self.size):
            fmt_row.addWidget(w)
        fmt_row.addStretch()
        form = QFormLayout()
        form.addRow("Трасса (линия):", self.line)
        form.addRow("", self.selected)
        form.addRow("Формат листа:", fmt_row)
        form.addRow("Масштаб 1:", self.scale)
        form.addRow("", self.rotate)
        form.addRow("Перекрытие листов:", self.overlap)
        form.addRow("Разрешение PDF, точек на дюйм:", self.dpi)
        form.addRow("Тема оформления:", self.theme)

        stamp = QGroupBox("Штамп (хранится в проекте)")
        stamp_form = QFormLayout(stamp)
        self.stamp = {}
        for key, label in STAMP_FIELDS:
            edit = QLineEdit(QgsProject.instance().readEntry(SCOPE, key, "")[0])
            # сразу в проект: набранное не пропадёт, если закрыть окно без разбивки
            edit.textChanged.connect(
                lambda text, key=key: QgsProject.instance().writeEntry(SCOPE, key, text.strip()))
            stamp_form.addRow(label + ":", edit)
            self.stamp[key] = edit

        self.split_btn = QPushButton("Разбить на листы")
        self.split_btn.setDefault(True)
        self.split_btn.clicked.connect(self.split)

        # готовые альбомы проекта: открыть, обновить штамп, вывести в PDF
        ready = QGroupBox("Готовый альбом")
        self.album = QComboBox()
        self.open_btn = QPushButton("Открыть макет")
        self.stamp_btn = QPushButton("Обновить штамп")
        self.stamp_btn.setToolTip("Переписать графы штампа из полей выше; листы и ваши правки в макете не меняются")
        self.pdf_btn = QPushButton("Сохранить PDF…")
        self.open_btn.clicked.connect(self.open_layout)
        self.stamp_btn.clicked.connect(self.refresh_stamp)
        self.pdf_btn.clicked.connect(self.save_pdf)
        ready_box = QVBoxLayout(ready)
        ready_box.addWidget(self.album)
        ready_row = QHBoxLayout()
        for b in (self.open_btn, self.stamp_btn, self.pdf_btn):
            ready_row.addWidget(b)
        ready_box.addLayout(ready_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")   # без перевода Qt — «Close»
        buttons.rejected.connect(self.reject)
        actions = QHBoxLayout()
        actions.addWidget(self.split_btn)
        actions.addStretch()
        actions.addWidget(buttons)

        box = QVBoxLayout(self)
        box.addLayout(form)
        box.addWidget(stamp)
        box.addLayout(actions)
        box.addWidget(ready)
        self.fmt.currentIndexChanged.connect(self.update_size)
        self.mult.valueChanged.connect(self.update_size)
        self.update_size()
        self.load_albums()

    def showEvent(self, event):   # проект могли сменить, пока окно было закрыто
        self.load_albums()
        for key, edit in self.stamp.items():
            value = QgsProject.instance().readEntry(SCOPE, key, "")[0]
            if edit.text().strip() != value:   # иначе проект зря помечается изменённым
                edit.setText(value)
        super().showEvent(event)

    def message(self, text, level=Qgis.MessageLevel.Info, duration=8):
        self.iface.messageBar().pushMessage(TITLE, text, level, duration)

    def update_size(self):
        w, h = core.page_size(list(core.BASE)[self.fmt.currentIndex()], self.mult.value())
        self.size.setText("{}×{} мм".format(w, h))

    def load_albums(self, current=None):
        current = current or self.album.currentText()
        self.album.clear()
        self.album.addItems(layout.albums(QgsProject.instance()))
        if current:
            self.album.setCurrentText(current)
        for b in (self.open_btn, self.stamp_btn, self.pdf_btn):
            b.setEnabled(self.album.count() > 0)
        if self.album.count() == 0:
            self.album.addItem("— пока нет: нажмите «Разбить на листы» —")
            self.album.setEnabled(False)
        else:
            self.album.setEnabled(True)

    def current_layout(self):
        return QgsProject.instance().layoutManager().layoutByName(self.album.currentText())

    def stamp_values(self):
        return {key: edit.text().strip() for key, edit in self.stamp.items()}

    def scale_value(self):
        text = self.scale.currentText().replace(" ", "").replace("1:", "")
        return int(text) if text.isdigit() and int(text) > 0 else None

    def split(self):
        import processing

        lyr = self.line.currentLayer()
        if lyr is None:
            self.message("Добавьте в проект слой с линией трассы.", Qgis.MessageLevel.Warning)
            return
        only_selected = self.selected.isChecked()
        if only_selected and lyr.selectedFeatureCount() == 0:
            self.message("На слое «{}» ничего не выделено: выделите трассу или снимите флажок "
                         "«Только выделенные объекты».".format(lyr.name()), Qgis.MessageLevel.Warning)
            return
        scale = self.scale_value()
        if scale is None:
            self.message("Масштаб — целое число, например 2000.", Qgis.MessageLevel.Warning)
            return
        fmt, mult = list(core.BASE)[self.fmt.currentIndex()], self.mult.value()
        rotate = self.rotate.isChecked()
        self.remember(fmt, mult, scale, rotate)
        try:
            out = processing.run("stripsheets:sheets", {
                "INPUT": QgsProcessingFeatureSourceDefinition(lyr.id(), only_selected),
                "FORMAT": self.fmt.currentIndex(), "MULT": mult, "SCALE": scale, "ROTATE": rotate,
                "OVERLAP": self.overlap.value(), "OUTPUT": "memory:"})["OUTPUT"]
        except Exception as err:   # QgsProcessingException: текст уже понятный
            self.message(str(err).strip(), Qgis.MessageLevel.Critical, 0)
            return
        project = QgsProject.instance()
        name = "Листы — {}".format(lyr.name())
        old_album = project.layoutManager().layoutByName(name)
        if old_album is not None and old_album.customProperty(layout.MARK) and not self.confirm_rebuild(name):
            return
        for old in project.mapLayersByName(name):   # прежнюю разбивку этой трассы заменяем
            if old.customProperty(SCOPE):
                project.removeMapLayer(old)
        count = out.featureCount()
        saved, failed = layout.save_sheets(project, out, name)
        if saved is not None:
            out = saved
        out.setName(name)
        out.setCustomProperty(SCOPE, True)
        layout.style_sheets(out)
        project.addMapLayer(out)
        theme = layout.make_theme(project, self.theme.text().strip(), name)
        lay = layout.build(project, out, fmt, mult, scale, rotate, self.stamp_values(), name, theme)
        self.load_albums(lay.name())
        if saved is not None:
            where = "Листы записаны в {} рядом с проектом.".format(os.path.basename(out.source().split("|")[0]))
        elif failed:
            where = ("Не удалось записать листы в {}: папка проекта недоступна для записи или файл занят. "
                     "Слой листов пока временный.".format(failed))
        else:
            where = ("Проект не сохранён, поэтому слой листов временный: сохраните проект и разбейте ещё раз, "
                     "иначе после закрытия QGIS листов не будет.")
        self.message("Готово: {} формата {}. Макет «{}» — в «Проект → Макеты», карты по теме «{}». {} "
                     "Листы можно подвинуть на карте, дальше — «Сохранить PDF…».".format(
                         _sheets(count), self.size.text(), lay.name(), theme, where),
                     Qgis.MessageLevel.Success if saved is not None else Qgis.MessageLevel.Warning, 15)

    def rebuild_box(self, name):
        """Вопрос перед пересборкой готового альбома (отдельно от показа — чтобы снимать окно)."""
        box = QMessageBox(self)
        box.setWindowTitle(TITLE)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("Альбом «{}» уже есть. Собрать его заново?".format(name))
        box.setInformativeText("Листы и макет соберутся с нуля. Ручные правки в макете пропадут, штамп сохранится.\n\n"
                               "Поменять только штамп — кнопка «Обновить штамп».")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        box.button(QMessageBox.StandardButton.Yes).setText("Собрать заново")
        box.button(QMessageBox.StandardButton.Cancel).setText("Отмена")
        return box

    def confirm_rebuild(self, name):
        return self.rebuild_box(name).exec() == QMessageBox.StandardButton.Yes

    def remember(self, fmt, mult, scale, rotate):
        settings = QgsSettings()
        settings.setValue(SCOPE + "/format", self.fmt.currentIndex())
        settings.setValue(SCOPE + "/mult", mult)
        settings.setValue(SCOPE + "/scale", str(scale))
        settings.setValue(SCOPE + "/rotate", rotate)
        settings.setValue(SCOPE + "/overlap", self.overlap.value())

    def open_layout(self):
        lay = self.current_layout()
        if lay is not None:
            self.iface.openLayoutDesigner(lay)   # атлас включает модуль при открытии любого своего макета

    def refresh_stamp(self):
        lay = self.current_layout()
        if lay is None:
            return
        layout.update_stamp(lay, self.stamp_values())
        self.message("Штамп в альбоме «{}» обновлён.".format(lay.name()), Qgis.MessageLevel.Success, 5)

    def save_pdf(self):
        lay = self.current_layout()
        if lay is None:
            self.message("Сначала нажмите «Разбить на листы».", Qgis.MessageLevel.Warning)
            self.load_albums()
            return
        folder = QgsSettings().value(SCOPE + "/pdf_dir", QgsProject.instance().homePath())
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить листы в PDF",
                                              "{}/{}.pdf".format(folder, lay.name()), "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        QgsSettings().setValue(SCOPE + "/pdf_dir", path.rsplit("/", 1)[0])
        QgsSettings().setValue(SCOPE + "/dpi", self.dpi.currentText())
        error = layout.export_pdf(lay, path, int(self.dpi.currentText()))
        if error:
            self.message("PDF не сохранён: {}. Проверьте, что файл не открыт в другой программе, "
                         "и повторите.".format(error), Qgis.MessageLevel.Critical, 0)
            return
        size = os.path.getsize(path) / 1024 / 1024
        self.message("PDF сохранён ({:.1f} МБ): {}".format(size, path).replace(".", ",", 1),
                     Qgis.MessageLevel.Success)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def _sheets(n):
    if n % 10 == 1 and n % 100 != 11:
        return "{} лист".format(n)
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "{} листа".format(n)
    return "{} листов".format(n)
