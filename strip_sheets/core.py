"""Разбивка линии на листы: форматы ГОСТ 2.301 и прямоугольники листов вдоль линии."""
import math

from qgis.core import QgsGeometry, QgsPointXY

# основные форматы: короткая и длинная сторона, мм
BASE = {"A4": (210, 297), "A3": (297, 420), "A2": (420, 594), "A1": (594, 841), "A0": (841, 1189)}
# поля рамки по ГОСТ Р 21.101: слева 20 мм, с остальных сторон 5 мм
FRAME_LEFT, FRAME = 20, 5
# трасса держится дальше от края: снизу штамп формы 3 высотой 55 мм
PAD_X, PAD_Y = 15, 60


# круглые шаги географической сетки, угловые секунды: 1″ … 1°
GRID_STEPS = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600]
GRID_MIN_MM = 50   # линии сетки на бумаге не чаще, чем через 5 см


def grid_step(scale, lat):
    """Шаг сетки по долготе и широте, градусы: самый мелкий круглый шаг,
    при котором линии на бумаге не ближе GRID_MIN_MM."""
    metres = GRID_MIN_MM / 1000.0 * scale          # столько метров на местности между линиями
    sec_lat = 30.87                                  # 1″ широты, м
    sec_lon = sec_lat * max(math.cos(math.radians(lat)), 0.01)

    def pick(sec_len):
        return next((s for s in GRID_STEPS if s * sec_len >= metres), GRID_STEPS[-1]) / 3600.0

    return pick(sec_lon), pick(sec_lat)


def page_size(fmt, mult):
    """Лист альбомной ориентации, мм: А3×3 = 891×420, А3 = 420×297."""
    short, long_ = BASE[fmt]
    if mult == 1:
        return long_, short
    return short * mult, long_


def map_size(fmt, mult):
    """Поле карты внутри рамки, мм."""
    w, h = page_size(fmt, mult)
    return w - FRAME_LEFT - FRAME, h - 2 * FRAME


def _fit(line, start, end, w, h, rotate):
    """Помещается ли участок [start, end] в прямоугольник w×h.
    Возвращает (угол, центр) или None."""
    part = line.constGet().curveSubstring(start, end)
    part = QgsGeometry(part)
    p0, p1 = line.interpolate(start).asPoint(), line.interpolate(end).asPoint()
    angle = 0.0
    if rotate and (p0.x() != p1.x() or p0.y() != p1.y()):
        angle = math.degrees(math.atan2(p1.y() - p0.y(), p1.x() - p0.x()))
        if angle > 90:          # север должен смотреть вверх, а не вниз
            angle -= 180
        elif angle < -90:
            angle += 180
    part.rotate(angle, p0)      # rotate() крутит по часовой: участок ложится горизонтально
    box = part.boundingBox()
    if box.width() > w + 1e-3 or box.height() > h + 1e-3:   # 1 мм на местности — не повод для лишнего листа
        return None
    center = QgsGeometry.fromPointXY(box.center())
    center.rotate(-angle, p0)
    return angle, center.asPoint()


def sheet_polygon(center, angle, w, h):
    """Прямоугольник листа w×h с центром center, повёрнутый на angle° против часовой."""
    x, y = center.x(), center.y()
    rect = QgsGeometry.fromPolygonXY([[
        QgsPointXY(x - w / 2, y - h / 2), QgsPointXY(x + w / 2, y - h / 2),
        QgsPointXY(x + w / 2, y + h / 2), QgsPointXY(x - w / 2, y + h / 2),
        QgsPointXY(x - w / 2, y - h / 2)]])
    rect.rotate(-angle, center)
    return rect


def split_line(line, sheet_w, sheet_h, rotate=True, overlap=0.0, pad_x=0.0, pad_y=0.0):
    """Листы вдоль одной линии (длины в единицах СК линии, обычно метры).
    sheet_w × sheet_h — охват листа на местности, pad_* — отступ линии от края листа.
    Возвращает [(полигон, угол°, от, до)] по порядку от начала линии."""
    w, h = sheet_w - 2 * pad_x, sheet_h - 2 * pad_y
    if w <= 0 or h <= 0:
        raise ValueError("лист слишком мал для отступов")
    total = line.length()
    sheets, start = [], 0.0
    while True:
        if _fit(line, start, total, w, h, rotate):
            end = total
        else:   # самый дальний конец участка, который ещё помещается
            lo, hi = start, total
            for _ in range(40):
                mid = (lo + hi) / 2
                if _fit(line, start, mid, w, h, rotate):
                    lo = mid
                else:
                    hi = mid
            end = lo
        angle, center = _fit(line, start, end, w, h, rotate)
        sheets.append((sheet_polygon(center, angle, sheet_w, sheet_h), angle, start, end))
        if end >= total:
            return sheets
        # следующий лист заходит на предыдущий на overlap, но не больше чем на четверть:
        # иначе при большом перекрытии листов становится вдвое больше
        start = max(end - overlap, start + (end - start) * 0.75)
