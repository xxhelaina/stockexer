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
        self._drag_x = None

    def focusInEvent(self, event):
        self.setStyleSheet(self.FOCUSED_STYLE)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.setStyleSheet(self.UNFOCUSED_STYLE)
        super().focusOutEvent(event)

    def resizeEvent(self, event):
        # 旧背景的像素尺寸与坐标轴位置失效，不能继续用于局部刷新。
        self.parent_window._cursor_backgrounds.clear()
        super().resizeEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if not self.parent_window._check_training_active():
            event.ignore()
            return

        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key == Qt.Key.Key_Z:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.parent_window.redo_drawing()
            else:
                self.parent_window.undo_drawing()
            event.accept()
        elif ctrl and key == Qt.Key.Key_Y:
            self.parent_window.redo_drawing()
            event.accept()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.parent_window.delete_selected_drawing()
            event.accept()
        elif key == Qt.Key.Key_Escape:
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
            self.parent_window._pan_left(1)
            event.accept()
        elif key == Qt.Key.Key_Right:
            self.parent_window._pan_right(1)
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if not self.parent_window._is_training_active_silent():
            event.ignore()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            coords = self._data_coords(event)
            if coords is not None and self.parent_window.drawing_mode != 'cursor':
                self.parent_window.add_drawing_point(*coords)
                event.accept()
                return
            if coords is not None and self.parent_window.begin_drawing_drag(*coords):
                event.accept()
                return
            self._drag_x = event.position().x()
            if self._try_move_cursor_from_event(event):
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.parent_window._is_training_active_silent():
            event.ignore()
            return

        coords = self._data_coords(event)
        if self.parent_window._drawing_drag is not None:
            if coords is not None:
                self.parent_window.move_drawing_drag(*coords)
            event.accept()
            return
        if self.parent_window.update_drawing_preview(coords):
            event.accept()
            return

        if self._drag_x is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.position().x() - self._drag_x
            width = self.parent_window.ax_kline.bbox.width / self.device_pixel_ratio
            count = self.parent_window.display_end_idx - self.parent_window.display_start_idx + 1
            steps = int(-delta * count / max(1, width))
            if steps:
                self._drag_x = event.position().x()
                self.parent_window._pan(steps)
            event.accept()
            return

        if self.parent_window.cursor_mode:
            if self._try_move_cursor_from_event(event):
                event.accept()
                return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_x = None
        if event.button() == Qt.MouseButton.LeftButton:
            if self.parent_window._drawing_drag is not None:
                coords = self._data_coords(event)
                if coords is not None:
                    self.parent_window.move_drawing_drag(*coords)
                self.parent_window.finish_drawing_drag()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._drag_x = None
        if self.parent_window._is_training_active_silent():
            if self.parent_window.drawing_mode == 'cursor' and self.parent_window._drawing_drag is None:
                self.parent_window.toggle_cursor_mode()
            event.accept()

    def wheelEvent(self, event):
        if self.parent_window._is_training_active_silent() and event.angleDelta().y():
            self.parent_window.finish_drawing_drag()
            self.setFocus()
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                pixels = self.mouseEventCoords(event)
                anchor = None
                for axis in (self.parent_window.ax_kline, self.parent_window.ax_volume, self.parent_window.ax_macd):
                    if axis.get_visible() and axis.bbox.contains(*pixels):
                        anchor = float(axis.transData.inverted().transform(pixels)[0])
                        break
                self.parent_window._zoom(.8 if event.angleDelta().y() > 0 else 1.2, anchor_x=anchor)
                if self.parent_window.cursor_mode:
                    self._try_move_cursor_from_event(event)
            else:
                self.parent_window._zoom_in() if event.angleDelta().y() > 0 else self.parent_window._zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    def _data_coords(self, event):
        ax = self.parent_window.ax_kline
        pixels = self.mouseEventCoords(event)
        if not ax.get_visible() or not ax.bbox.contains(*pixels):
            return None
        return ax.transData.inverted().transform(pixels)

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
            x_data, price = inv.transform((x_pixel, y_pixel))

            start_idx = self.parent_window.display_start_idx
            end_idx = self.parent_window.display_end_idx
            data_len = end_idx - start_idx + 1
            # 坐标轴范围是 [-0.5, data_len-0.5]。旧判断只接受
            # [0, data_len-1]，会丢掉首尾 K 线各半个柱宽的区域。
            if -0.5 <= x_data <= data_len - 0.5:
                self.parent_window._move_cursor_to_data_x(x_data, fast=True, price=float(price))
                return True
        except Exception:
            pass

        return False
