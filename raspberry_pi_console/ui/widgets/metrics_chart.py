# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPointF, Qt, QRect
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from raspberry_pi_console.core.metrics_history import fetch_series

_Y_TICKS = (100, 75, 50, 25, 0)
_LEFT_MARGIN = 44
_TOP_MARGIN = 28
_RIGHT_MARGIN = 12
_BOTTOM_MARGIN = 20

_METRICS = (
    ("cpu", "CPU", QColor("#3b82f6"), "%"),
    ("mem", "内存", QColor("#22c55e"), "%"),
    ("temp", "温度", QColor("#ef4444"), "°C"),
)


class MetricsChartWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._series: list[dict] = []
        self._hover_index: int | None = None
        self._chart_rect = QRect()
        self.setMinimumHeight(260)
        self.setMouseTracking(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.legend = QLabel("CPU / 内存 / 温度 — 最近采样（鼠标悬停查看数值）")
        self.legend.setObjectName("SubTitle")
        layout.addWidget(self.legend)
        layout.addStretch(1)

    def set_host(self, host: str, limit: int = 120) -> None:
        self._series = fetch_series(host, limit=limit)
        self._hover_index = None
        self.update()

    def _plot_rect(self) -> QRect:
        top = self.legend.geometry().bottom() + 6 if self.legend.isVisible() else _TOP_MARGIN
        if top < _TOP_MARGIN:
            top = _TOP_MARGIN
        return self.rect().adjusted(_LEFT_MARGIN, top, -_RIGHT_MARGIN, -_BOTTOM_MARGIN)

    def _value_to_y(self, value: float, plot: QRect) -> float:
        clamped = max(0.0, min(float(value), 100.0))
        return plot.bottom() - (plot.height() * clamped / 100.0)

    def _index_to_x(self, index: int, plot: QRect) -> float:
        count = len(self._series)
        if count <= 1:
            return float(plot.left())
        return plot.left() + (plot.width() * index / (count - 1))

    def _index_at_x(self, x: float) -> int | None:
        if len(self._series) < 2:
            return None
        plot = self._plot_rect()
        if not plot.contains(int(x), plot.center().y()):
            return None
        ratio = (x - plot.left()) / max(1, plot.width())
        index = int(round(ratio * (len(self._series) - 1)))
        return max(0, min(index, len(self._series) - 1))

    def _metric_points(self, key: str, plot: QRect) -> list[QPointF]:
        points: list[QPointF] = []
        for index, row in enumerate(self._series):
            value = float(row.get(key) or 0)
            points.append(QPointF(self._index_to_x(index, plot), self._value_to_y(value, plot)))
        return points

    def _draw_y_axis(self, painter: QPainter, plot: QRect) -> None:
        font = QFont(self.font())
        font.setPointSize(max(8, font.pointSize() - 1))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        grid_pen = QPen(QColor("#e2e8f0"))
        grid_pen.setStyle(Qt.DotLine)
        label_pen = QPen(QColor("#64748b"))

        for tick in _Y_TICKS:
            y = int(self._value_to_y(tick, plot))
            label = str(tick)
            label_width = metrics.horizontalAdvance(label)
            label_x = plot.left() - label_width - 8
            label_y = y + metrics.ascent() // 2 - 1
            painter.setPen(label_pen)
            painter.drawText(label_x, label_y, label)

            painter.setPen(grid_pen)
            painter.drawLine(plot.left(), y, plot.right(), y)

    def _draw_hover(self, painter: QPainter, plot: QRect, index: int) -> None:
        row = self._series[index]
        x = int(self._index_to_x(index, plot))

        cross_pen = QPen(QColor("#94a3b8"))
        cross_pen.setStyle(Qt.DashLine)
        painter.setPen(cross_pen)
        painter.drawLine(x, plot.top(), x, plot.bottom())

        lines = []
        ts = float(row.get("ts") or 0)
        if ts > 0:
            lines.append(f"时间  {datetime.fromtimestamp(ts).strftime('%H:%M:%S')}")
        for key, label, color, suffix in _METRICS:
            value = float(row.get(key) or 0)
            if suffix == "%":
                lines.append(f"{label}  {value:.0f}{suffix}")
            else:
                lines.append(f"{label}  {value:.0f}{suffix}")
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(color, 2))
            y = int(self._value_to_y(value, plot))
            painter.drawEllipse(QPointF(x, y), 4, 4)

        if not lines:
            return

        font = QFont(self.font())
        font.setPointSize(max(9, font.pointSize()))
        painter.setFont(font)
        fm = QFontMetrics(font)
        padding = 8
        line_height = fm.height()
        box_width = max(fm.horizontalAdvance(line) for line in lines) + padding * 2
        box_height = line_height * len(lines) + padding * 2

        box_x = x + 10
        if box_x + box_width > plot.right():
            box_x = x - box_width - 10
        box_y = plot.top() + 6
        box_rect = QRect(int(box_x), int(box_y), int(box_width), int(box_height))

        painter.setPen(QPen(QColor("#cbd5e1")))
        painter.setBrush(QBrush(QColor(255, 255, 255, 245)))
        painter.drawRoundedRect(box_rect, 6, 6)

        painter.setPen(QPen(QColor("#1e293b")))
        text_y = box_rect.top() + padding + fm.ascent()
        for line in lines:
            painter.drawText(box_rect.left() + padding, text_y, line)
            text_y += line_height

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self._index_at_x(event.position().x())
        if index != self._hover_index:
            self._hover_index = index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_index is not None:
            self._hover_index = None
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        plot = self._plot_rect()
        self._chart_rect = plot

        painter.fillRect(self.rect(), QColor("#f8fafc"))
        painter.setPen(QPen(QColor("#cbd5e1")))
        painter.drawRect(plot)

        if len(self._series) < 2:
            painter.setPen(QColor("#64748b"))
            painter.drawText(plot, Qt.AlignCenter, "刷新仪表盘后将记录趋势数据")
            return

        self._draw_y_axis(painter, plot)

        for key, _label, color, _suffix in _METRICS:
            points = self._metric_points(key, plot)
            pen = QPen(color, 2)
            painter.setPen(pen)
            for idx in range(1, len(points)):
                painter.drawLine(points[idx - 1], points[idx])

        if self._hover_index is not None:
            self._draw_hover(painter, plot, self._hover_index)

        painter.setPen(QPen(QColor("#64748b")))
        font = QFont(self.font())
        font.setPointSize(max(8, font.pointSize() - 1))
        painter.setFont(font)
        painter.drawText(
            plot.left(),
            self.rect().bottom() - 4,
            plot.width(),
            16,
            Qt.AlignHCenter,
            "较早 ← 时间 → 较新",
        )
