"""Small QPainter charts. Every chart is fed stored data and paints an explicit empty state when there is none.
Each exposes a plain-text summary through its accessible description."""
from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

KIND_COLORS = {"insert": 0, "paste": 0, "import": 0, "delete": 4, "replace": 1, "pause": 2, "resume": 2, "checkpoint": 3,
               "interruption": 4, "cursor_move": 5, "external": 5}


class ChartBase(QWidget):
    def __init__(self, title: str, empty_text: str = "No data to chart.", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.empty_text = empty_text
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAccessibleName(title)
        self._empty = True

    def set_summary(self, text: str) -> None:
        self.setAccessibleDescription(text)
        self.setToolTip(text)

    def _begin(self, p: QPainter) -> QRectF:
        c = theme.colors()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(c["surface"]))
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(c["text"]))
        p.drawText(QRectF(10, 4, self.width() - 20, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        f.setBold(False)
        f.setPointSizeF(max(7.5, f.pointSizeF() - 2))
        p.setFont(f)
        return QRectF(60, 30, self.width() - 76, self.height() - 74)

    def _paint_empty(self, p: QPainter) -> None:
        p.setPen(QColor(theme.colors()["muted"]))
        p.drawText(self.rect().adjusted(10, 30, -10, -10), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)

    @staticmethod
    def _fmt(v: float) -> str:
        if abs(v) >= 10 or v == int(v):
            return f"{v:,.0f}"
        return f"{v:.2g}"

    def _axes(self, p: QPainter, r: QRectF, ymax: float, xlabel: str = "", ylabel: str = "") -> None:
        c = theme.colors()
        p.setPen(QPen(QColor(c["line"]), 1))
        for i in range(5):
            y = r.bottom() - r.height() * i / 4
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setPen(QColor(c["muted"]))
        for i in range(5):
            y = r.bottom() - r.height() * i / 4
            p.drawText(QRectF(0, y - 8, r.left() - 4, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._fmt(ymax * i / 4))
        if xlabel:
            p.drawText(QRectF(r.left(), self.height() - 18, r.width(), 16), Qt.AlignmentFlag.AlignCenter, xlabel)
        if ylabel:
            p.save()
            p.translate(12, r.center().y())
            p.rotate(-90)
            p.drawText(QRectF(-r.height() / 2, -8, r.height(), 16), Qt.AlignmentFlag.AlignCenter, ylabel)
            p.restore()


class LineChart(ChartBase):
    def __init__(self, title: str, xlabel: str = "", ylabel: str = "", empty_text: str = "No timestamped data recorded.") -> None:
        super().__init__(title, empty_text)
        self.xlabel, self.ylabel = xlabel, ylabel
        self.points: list[tuple[float, float]] = []

    def set_points(self, pts: Sequence[tuple[float, float]]) -> None:
        self.points = list(pts)
        if self.points:
            self.set_summary(f"{self.title}: {len(self.points)} points, y from {min(y for _, y in self.points):g} to {max(y for _, y in self.points):g}.")
        else:
            self.set_summary(f"{self.title}: no data. {self.empty_text}")
        self.update()

    def paintEvent(self, _e: object) -> None:
        p = QPainter(self)
        r = self._begin(p)
        if not self.points:
            self._paint_empty(p)
            return
        xs, ys = [x for x, _ in self.points], [y for _, y in self.points]
        x0, x1, ymax = min(xs), max(xs), max(max(ys), 1)
        span = (x1 - x0) or 1
        self._axes(p, r, ymax, self.xlabel, self.ylabel)
        pen = QPen(QColor(theme.series()[0]), 2)
        p.setPen(pen)
        prev = None
        for x, y in self.points:  # step line: text length is constant between events
            pt = QPointF(r.left() + (x - x0) / span * r.width(), r.bottom() - y / ymax * r.height())
            if prev is not None:
                p.drawLine(prev, QPointF(pt.x(), prev.y()))
                p.drawLine(QPointF(pt.x(), prev.y()), pt)
            prev = pt
        p.setPen(QColor(theme.colors()["muted"]))
        p.drawText(QRectF(r.left(), r.bottom() + 2, 80, 14), Qt.AlignmentFlag.AlignLeft, f"{x0 / 1000:.0f}s")
        p.drawText(QRectF(r.right() - 80, r.bottom() + 2, 80, 14), Qt.AlignmentFlag.AlignRight, f"{x1 / 1000:.0f}s")


class BarChart(ChartBase):
    def __init__(self, title: str, ylabel: str = "", empty_text: str = "No data to chart.") -> None:
        super().__init__(title, empty_text)
        self.ylabel = ylabel
        self.cats: list[str] = []
        self.series: dict[str, list[float | None]] = {}

    def set_data(self, cats: list[str], series: dict[str, list[float | None]]) -> None:
        self.cats, self.series = cats, series
        has = any(v is not None for vs in series.values() for v in vs)
        if has:
            parts = [f"{n}: " + ", ".join(f"{c}={'n/a' if v is None else f'{v:.4g}'}" for c, v in zip(cats, vs, strict=False)) for n, vs in series.items()]
            self.set_summary(f"{self.title}. " + "; ".join(parts))
        else:
            self.set_summary(f"{self.title}: no data. {self.empty_text}")
        self.update()

    def paintEvent(self, _e: object) -> None:
        p = QPainter(self)
        r = self._begin(p)
        vals = [v for vs in self.series.values() for v in vs if v is not None]
        if not vals:
            self._paint_empty(p)
            return
        ymax = max(max(vals), 1e-9)
        self._axes(p, r, ymax, "", self.ylabel)
        n, k = len(self.cats), max(1, len(self.series))
        slot = r.width() / max(1, n)
        bw = min(34.0, slot * 0.8 / k)
        cols = theme.series()
        for si, (_name, vs) in enumerate(self.series.items()):
            for ci, v in enumerate(vs):
                if v is None:
                    continue
                x = r.left() + ci * slot + slot / 2 - bw * k / 2 + si * bw
                h = v / ymax * r.height()
                p.fillRect(QRectF(x, r.bottom() - h, bw - 2, h), QColor(cols[si % len(cols)]))
        p.setPen(QColor(theme.colors()["muted"]))
        for ci, c in enumerate(self.cats):
            p.drawText(QRectF(r.left() + ci * slot, r.bottom() + 2, slot, 16), Qt.AlignmentFlag.AlignCenter, c)
        if len(self.series) > 1:
            x = r.left() + 160
            for si, name in enumerate(self.series):
                p.fillRect(QRectF(x, 8, 10, 10), QColor(cols[si % len(cols)]))
                p.setPen(QColor(theme.colors()["text"]))
                p.drawText(QRectF(x + 14, 4, 110, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
                x += 120


class TimelineChart(ChartBase):
    def __init__(self) -> None:
        super().__init__("Event timeline", "Not recorded: this session has no event timestamps.")
        self.setMinimumHeight(120)
        self.items: list[tuple[float, str]] = []

    def set_events(self, items: list[tuple[int | None, str]]) -> None:
        self.items = [(float(t), k) for t, k in items if t is not None]
        total, timed = len(items), len(self.items)
        if timed:
            self.set_summary(f"Event timeline with {timed} timestamped events of {total}; {total - timed} not recorded.")
        else:
            self.set_summary("Event timeline: not recorded.")
        self.update()

    def paintEvent(self, _e: object) -> None:
        p = QPainter(self)
        r = self._begin(p)
        if not self.items:
            self._paint_empty(p)
            return
        t0, t1 = min(t for t, _ in self.items), max(t for t, _ in self.items)
        span = (t1 - t0) or 1
        p.setPen(QPen(QColor(theme.colors()["line"]), 1))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
        cols = theme.series()
        for t, k in self.items:
            x = r.left() + (t - t0) / span * r.width()
            h = 0.25 + 0.65 * (1.0 if k in ("delete", "replace") else 0.6 if k in ("insert", "paste", "import") else 0.4)
            p.setPen(QPen(QColor(cols[KIND_COLORS.get(k, 5) % len(cols)]), 2))
            p.drawLine(QPointF(x, r.bottom()), QPointF(x, r.bottom() - h * r.height()))
        p.setPen(QColor(theme.colors()["muted"]))
        p.drawText(QRectF(r.left(), r.bottom() + 2, 90, 14), Qt.AlignmentFlag.AlignLeft, f"{t0 / 1000:.1f}s")
        p.drawText(QRectF(r.right() - 90, r.bottom() + 2, 90, 14), Qt.AlignmentFlag.AlignRight, f"{t1 / 1000:.1f}s")
        x = r.left() + 130
        seen = []
        for _, k in self.items:
            if k not in seen:
                seen.append(k)
        for k in seen[:8]:
            p.fillRect(QRectF(x, 8, 10, 10), QColor(cols[KIND_COLORS.get(k, 5) % len(cols)]))
            p.setPen(QColor(theme.colors()["text"]))
            p.drawText(QRectF(x + 14, 4, 90, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, k)
            x += 100


class ConfusionWidget(ChartBase):
    def __init__(self) -> None:
        super().__init__("Confusion matrix (test split)", "Run an evaluation to see results.")
        self.setMinimumHeight(200)
        self.cm: dict[str, int] | None = None

    def set_matrix(self, cm: dict[str, int] | None) -> None:
        self.cm = cm
        self.set_summary("No evaluation yet." if not cm else
                         f"Confusion matrix: true positives {cm['tp']}, false positives {cm['fp']}, false negatives {cm['fn']}, true negatives {cm['tn']}.")
        self.update()

    def paintEvent(self, _e: object) -> None:
        p = QPainter(self)
        r = self._begin(p)
        if not self.cm:
            self._paint_empty(p)
            return
        c = theme.colors()
        w, h = min(160, r.width() / 2 - 40), min(70, r.height() / 2)
        x0, y0 = r.left() + 110, r.top() + 22
        p.setPen(QColor(c["muted"]))
        p.drawText(QRectF(x0, r.top(), w, 18), Qt.AlignmentFlag.AlignCenter, "predicted +")
        p.drawText(QRectF(x0 + w, r.top(), w, 18), Qt.AlignmentFlag.AlignCenter, "predicted -")
        p.drawText(QRectF(r.left(), y0, 106, h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "actual +")
        p.drawText(QRectF(r.left(), y0 + h, 106, h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "actual -")
        cells = [(0, 0, "tp", True), (1, 0, "fn", False), (0, 1, "fp", False), (1, 1, "tn", True)]
        for cx, cy, key, good in cells:
            rect = QRectF(x0 + cx * w, y0 + cy * h, w - 2, h - 2)
            col = QColor(c["ins"] if good else c["del"])
            p.fillRect(rect, col)
            p.setPen(QColor(c["text"]))
            f = QFont(self.font())
            f.setPointSizeF(f.pointSizeF() + 6)
            f.setBold(True)
            p.setFont(f)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self.cm[key]}")
            f.setBold(False)
            f.setPointSizeF(f.pointSizeF() - 6)
            p.setFont(f)
            p.drawText(rect.adjusted(4, 2, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, key.upper())


class RhythmTimeline(ChartBase):
    """Writing intervals, short pauses and breaks on the simulated session clock. Segments are SIMULATED (engine);
    markers are real application telemetry (user pause/resume/cancel). The legend says which is which."""

    def __init__(self, title: str = "Writing and break schedule") -> None:
        super().__init__(title, "No schedule to show yet.")
        self.setMinimumHeight(138)
        self.segs: list[tuple[int, int, str]] = []
        self.total = 0
        self.markers: list[tuple[int, str]] = []
        self.now: float | None = None

    def set_schedule(self, segments: list, total_ms: int | None = None, markers: list[tuple[int, str]] | None = None) -> None:
        self.segs = [(s.start_ms, s.end_ms, s.kind) for s in segments]
        self.total = max(total_ms or 0, max((e for _, e, _ in self.segs), default=0))
        self.markers = list(markers or [])
        brk = [s for s in self.segs if s[2] == "break"]
        idle = sum(e - s for s, e, k in self.segs if k != "writing")
        self.set_summary(f"{self.title}: {len(self.segs)} segments over {self.total / 1000:.0f} s, {len(brk)} breaks, {idle / 1000:.0f} s idle. "
                         "All segments are simulated; markers are real user actions.")
        self.update()

    def set_now(self, ms: float | None) -> None:
        self.now = ms
        self.update()

    def paintEvent(self, _e: object) -> None:
        p = QPainter(self)
        c = theme.colors()
        cols = theme.series()
        r = self._begin(p)
        r = QRectF(r.left() - 36, r.top(), r.width() + 36, max(28.0, r.height() - 18))
        if not self.segs or self.total <= 0:
            self._paint_empty(p)
            return
        colors = {"writing": QColor(cols[0]), "short_pause": QColor(cols[3]), "break": QColor(cols[1])}
        bar = QRectF(r.left(), r.top() + 22, r.width(), 28)
        p.fillRect(bar, QColor(c["line"]))
        for s, e, k in self.segs:
            x0 = bar.left() + s / self.total * bar.width()
            w = max(1.0, (e - s) / self.total * bar.width())
            p.fillRect(QRectF(x0, bar.top(), w, bar.height()), colors.get(k, QColor(cols[0])))
        p.setPen(QColor(c["muted"]))
        p.drawText(QRectF(bar.left(), bar.bottom() + 2, 80, 14), Qt.AlignmentFlag.AlignLeft, "0:00")
        end = int(self.total / 1000)
        p.drawText(QRectF(bar.right() - 90, bar.bottom() + 2, 90, 14), Qt.AlignmentFlag.AlignRight, f"{end // 60}:{end % 60:02d}")
        p.setPen(QPen(QColor(c["text"]), 2))
        for ms, _label in self.markers:  # real application telemetry: diamonds above the bar
            x = bar.left() + min(ms, self.total) / self.total * bar.width()
            p.drawLine(QPointF(x, bar.top() - 6), QPointF(x, bar.bottom()))
            p.drawEllipse(QPointF(x, bar.top() - 8), 3, 3)
        if self.now is not None:
            x = bar.left() + min(self.now, self.total) / self.total * bar.width()
            p.setPen(QPen(QColor(c["accent"]), 2))
            p.drawLine(QPointF(x, bar.top() - 4), QPointF(x, bar.bottom() + 4))
        # legend: simulated vs real
        x = r.left()
        y = int(bar.bottom()) + 20
        for text, col in (("Writing (simulated)", colors["writing"]), ("Short pause (simulated)", colors["short_pause"]), ("Break (simulated)", colors["break"])):
            p.fillRect(QRectF(x, y + 2, 10, 10), col)
            p.setPen(QColor(c["text"]))
            w = p.fontMetrics().horizontalAdvance(text) + 22
            p.drawText(QRectF(x + 14, y, w, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            x += w + 8
        p.setPen(QPen(QColor(c["text"]), 2))
        p.drawEllipse(QPointF(x + 4, y + 7), 3, 3)
        p.setPen(QColor(c["text"]))
        p.drawText(QRectF(x + 12, y, 260, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Real user action (pause/resume/cancel)")
