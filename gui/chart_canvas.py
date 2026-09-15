# -*- coding: utf-8 -*-
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas


class MyFigureCanvas(FigureCanvas):
    """自定义 Matplotlib 画布，处理焦点、键盘和鼠标事件以与父窗口交互。"""

    # 图表本身已经位于卡片边框内；焦点时再画一圈高亮边会造成画布跳动，
    # 也会让最右侧的可用交互区域看起来少了几个像素。
    FOCUSED_STYLE = "border: none; background: #0D1116;"
    UNFOCUSED_STYLE = "border: none; background: #0D1116;"

    def __init__(self, figure, parent_window):
        super().__init__(figure)
        self.parent_window = parent_window
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setStyleSheet(self.UNFOCUSED_STYLE)

    def focusInEvent(self, event):
        self.setStyleSheet(self.FOCUSED_STYLE)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.setStyleSheet(self.UNFOCUSED_STYLE)
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if not self.parent_window._check_training_active():
            event.ignore()
            return

        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.parent_window.exit_cursor_mode()
            event.accept()
        elif key == Qt.Key.Key_F5:
            self.parent_window.toggle_fenshi_view()
            event.accept()
        elif key == Qt.Key.Key_Up:
            self.parent_window._zoom_in()
            event.accept()
        elif key == Qt.Key.Key_Down:
            self.parent_window._zoom_out()
            event.accept()
        elif key == Qt.Key.Key_Left:
            self.parent_window._move_cursor_left()
            event.accept()
        elif key == Qt.Key.Key_Right:
            self.parent_window._move_cursor_right()
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if not self.parent_window._is_training_active_silent():
            event.ignore()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if self._try_move_cursor_from_event(event):
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.parent_window._is_training_active_silent():
            event.ignore()
            return

        if self.parent_window.cursor_mode:
            if self._try_move_cursor_from_event(event):
                event.accept()
                return

        super().mouseMoveEvent(event)

    def _try_move_cursor_from_event(self, event):
        ax = self.parent_window.ax_kline
        if not ax.get_visible():
            return False

        try:
            # Qt 事件使用逻辑像素且原点在左上；Matplotlib 使用物理像素且
            # 原点在左下。后端自带的转换同时处理高 DPI 和 Y 轴翻转。
            x_pixel, y_pixel = self.mouseEventCoords(event)
            if not ax.bbox.contains(x_pixel, y_pixel):
                return False
            inv = ax.transData.inverted()
            x_data, _ = inv.transform((x_pixel, y_pixel))

            start_idx = self.parent_window.display_start_idx
            end_idx = self.parent_window.display_end_idx
            data_len = end_idx - start_idx + 1
            # 坐标轴范围是 [-0.5, data_len-0.5]。旧判断只接受
            # [0, data_len-1]，会丢掉首尾 K 线各半个柱宽的区域。
            if -0.5 <= x_data <= data_len - 0.5:
                self.parent_window._move_cursor_to_data_x(x_data, fast=True)
                return True
        except Exception:
            pass

        return False
