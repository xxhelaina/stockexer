"""Editable, timestamp-anchored chart drawings and bounded undo history."""
from copy import deepcopy

import numpy as np
import pandas as pd


class DrawingToolsMixin:
    def reset_drawing_interaction(self):
        self.drawing_mode = 'cursor'
        if hasattr(self, 'drawing_buttons'):
            for mode, button in self.drawing_buttons.items():
                button.setChecked(mode == 'cursor')
        self.selected_drawing = None
        self._drawing_drag = None
        self._trend_anchor = None
        self._trend_period = None
        self._drawing_preview = None
        self._drawing_artists = {}
        self._preview_artist = None
        self._drawing_undo = []
        self._drawing_redo = []
        self._update_drawing_controls()

    def _update_drawing_controls(self):
        if not hasattr(self, 'btn_drawing_delete'):
            return
        selected = self.selected_drawing is not None and self.selected_drawing < len(self.drawings)
        self.btn_drawing_delete.setEnabled(selected and not self.drawings[self.selected_drawing].get('locked', False))
        self.btn_drawing_undo.setEnabled(bool(self._drawing_undo))
        self.btn_drawing_redo.setEnabled(bool(self._drawing_redo))
        self.btn_drawing_lock.setEnabled(selected)
        self.btn_drawing_lock.setText('解锁' if selected and self.drawings[self.selected_drawing].get('locked') else '锁定')
        for control in (self.drawing_color, self.drawing_width, self.drawing_style):
            control.setEnabled(not selected or not self.drawings[self.selected_drawing].get('locked'))
        if selected:
            drawing = self.drawings[self.selected_drawing]
            for control in (self.drawing_color, self.drawing_width, self.drawing_style):
                control.blockSignals(True)
            color = self.drawing_color.findData(drawing.get('color', '#639CFF'))
            self.drawing_color.setCurrentIndex(max(0, color))
            self.drawing_width.setValue(drawing.get('width', 1.3))
            self.drawing_style.setCurrentIndex(max(0, self.drawing_style.findData(drawing.get('style', '-'))))
            for control in (self.drawing_color, self.drawing_width, self.drawing_style):
                control.blockSignals(False)

    def _remember_drawings(self, before=None):
        self._drawing_undo.append(deepcopy(self.drawings if before is None else before))
        self._drawing_undo = self._drawing_undo[-100:]
        self._drawing_redo.clear()

    def undo_drawing(self):
        self.cancel_drawing_gesture()
        if self._drawing_undo:
            self._drawing_redo.append(deepcopy(self.drawings))
            self.drawings = self._drawing_undo.pop()
            self.selected_drawing = None
        self._update_drawing_controls()
        self._draw_combined_chart()
        self.canvas.setFocus()

    def redo_drawing(self):
        self.cancel_drawing_gesture()
        if self._drawing_redo:
            self._drawing_undo.append(deepcopy(self.drawings))
            self.drawings = self._drawing_redo.pop()
            self.selected_drawing = None
        self._update_drawing_controls()
        self._draw_combined_chart()
        self.canvas.setFocus()

    def cancel_drawing_gesture(self):
        if self._drawing_drag is not None:
            self.drawings = self._drawing_drag['before']
        self._drawing_drag = None
        self._trend_anchor = None
        self._trend_period = None
        self._drawing_preview = None
        self.canvas._drag_x = None

    def _drawing_point(self, x, price):
        idx = int(np.clip(self.display_start_idx + np.floor(x + .5),
                          self.display_start_idx, self.display_end_idx))
        return [self.stock_data.index[idx].isoformat(), float(price)]

    def _drawing_xy(self, drawing):
        return np.array([[self.stock_data.index.searchsorted(pd.Timestamp(t)) - self.display_start_idx, p]
                         for t, p in drawing['points']], dtype=float)

    def current_drawing_style(self):
        return {'color': self.drawing_color.currentData(), 'width': self.drawing_width.value(),
                'style': self.drawing_style.currentData()}

    def apply_drawing_style(self, *_):
        if self.selected_drawing is not None:
            drawing = self.drawings[self.selected_drawing]
            if not drawing.get('locked') and any(drawing.get(k) != v for k, v in self.current_drawing_style().items()):
                self._remember_drawings()
                drawing.update(self.current_drawing_style())
                self._update_drawing_controls()
                self._draw_combined_chart()

    def toggle_drawing_lock(self):
        if self.selected_drawing is not None:
            self._remember_drawings()
            drawing = self.drawings[self.selected_drawing]
            drawing['locked'] = not drawing.get('locked', False)
            self._update_drawing_controls()
            self._draw_combined_chart()
            self.canvas.setFocus()

    def delete_selected_drawing(self):
        self.cancel_drawing_gesture()
        if self.selected_drawing is not None and not self.drawings[self.selected_drawing].get('locked'):
            self._remember_drawings()
            self.drawings.pop(self.selected_drawing)
            self.selected_drawing = None
            self._update_drawing_controls()
            self._draw_combined_chart()
        self.canvas.setFocus()

    def toggle_drawings_visible(self, *_):
        self.cancel_drawing_gesture()
        self.selected_drawing = None
        self._update_drawing_controls()
        self._draw_combined_chart()

    def begin_drawing_drag(self, x, price):
        if not self.show_drawings.isChecked():
            return False
        position = self.ax_kline.transData.transform((x, price))
        tolerance = 8 * self.canvas.device_pixel_ratio
        # Selected handles get priority, then the topmost visible segment.
        order = list(reversed(range(len(self.drawings))))
        if self.selected_drawing in order:
            order.remove(self.selected_drawing)
            order.insert(0, self.selected_drawing)
        for idx in order:
            drawing = self.drawings[idx]
            if drawing['period'] != self.current_period:
                continue
            pixels = self.ax_kline.transData.transform(self._drawing_xy(drawing))
            endpoint = None
            if drawing['type'] == 'horizontal':
                distance = abs(position[1] - pixels[0, 1])
            else:
                distances = np.linalg.norm(pixels - position, axis=1)
                if distances.min() <= tolerance:
                    endpoint = int(distances.argmin())
                vector = pixels[1] - pixels[0]
                fraction = np.clip(np.dot(position - pixels[0], vector) / max(np.dot(vector, vector), 1e-12), 0, 1)
                distance = np.linalg.norm(position - (pixels[0] + fraction * vector))
            if distance > tolerance:
                continue
            self.selected_drawing = idx
            self._update_drawing_controls()
            if not drawing.get('locked'):
                self.stop_playback()
                self._drawing_drag = {'index': idx, 'endpoint': endpoint, 'origin': (x, price),
                                      'start': self.display_start_idx, 'period': self.current_period,
                                      'before': deepcopy(self.drawings)}
            self._draw_combined_chart()
            return True
        if self.selected_drawing is not None:
            self.selected_drawing = None
            self._update_drawing_controls()
            self._draw_combined_chart()
        return False

    def move_drawing_drag(self, x, price):
        drag = self._drawing_drag
        if drag is None:
            return
        if drag['period'] != self.current_period or drag['start'] != self.display_start_idx:
            self.cancel_drawing_gesture()
            self._draw_combined_chart()
            return
        original = drag['before'][drag['index']]
        drawing = self.drawings[drag['index']]
        if drag['endpoint'] is not None:
            drawing['points'][drag['endpoint']] = self._drawing_point(x, price)
        else:
            indices = [int(self.stock_data.index.searchsorted(pd.Timestamp(p[0]))) for p in original['points']]
            delta = int(np.floor(x + .5) - np.floor(drag['origin'][0] + .5))
            delta = int(np.clip(delta, -min(indices), self.current_date_idx - max(indices)))
            drawing['points'] = [[self.stock_data.index[i + delta].isoformat(), p[1] + price - drag['origin'][1]]
                                 for i, p in zip(indices, original['points'])]
        self._refresh_drawing_artists(drag['index'])
        self._update_cursor_overlay()

    def finish_drawing_drag(self):
        if self._drawing_drag is not None:
            before = self._drawing_drag['before']
            self._drawing_drag = None
            if before != self.drawings:
                self._remember_drawings(before)
            self._update_drawing_controls()
            self._draw_combined_chart()

    def update_drawing_preview(self, coords):
        if self._trend_anchor is None:
            return False
        self._drawing_preview = self._drawing_point(*coords) if coords is not None else None
        if self._preview_artist is not None:
            self._refresh_preview_artist()
            self._update_cursor_overlay()
        return True

    def _refresh_drawing_artists(self, idx):
        artists = self._drawing_artists.get(idx)
        if not artists:
            return
        xy = self._drawing_xy(self.drawings[idx])
        if self.drawings[idx]['type'] == 'horizontal':
            artists[0].set_ydata([xy[0, 1], xy[0, 1]])
        else:
            artists[0].set_data(xy[:, 0], xy[:, 1])
        if len(artists) > 1:
            artists[1].set_data(xy[:, 0], xy[:, 1])

    def _refresh_preview_artist(self):
        visible = (self._trend_anchor is not None and self._trend_period == self.current_period
                   and self.show_drawings.isChecked())
        self._preview_artist.set_visible(visible)
        if visible:
            xy = self._drawing_xy({'points': [self._trend_anchor, self._drawing_preview or self._trend_anchor]})
            self._preview_artist.set_data(xy[:, 0], xy[:, 1])

    def _render_drawings(self):
        self._drawing_artists = {}
        if self.show_drawings.isChecked():
            for idx, drawing in enumerate(self.drawings):
                if drawing['period'] != self.current_period:
                    continue
                xy = self._drawing_xy(drawing)
                animated = self._drawing_drag is not None and self._drawing_drag['index'] == idx
                style = dict(color=drawing.get('color', '#639CFF'), linewidth=drawing.get('width', 1.3),
                             linestyle=drawing.get('style', '-'), zorder=14, animated=animated)
                line = (self.ax_kline.axhline(xy[0, 1], **style) if drawing['type'] == 'horizontal'
                        else self.ax_kline.plot(xy[:, 0], xy[:, 1], **style)[0])
                artists = [line]
                if idx == self.selected_drawing:
                    handles = self.ax_kline.plot(xy[:, 0], xy[:, 1], linestyle='none', marker='o', markersize=6,
                        markerfacecolor='#0D1116', markeredgecolor=style['color'], zorder=16, animated=animated)[0]
                    artists.append(handles)
                self._drawing_artists[idx] = artists
        self._preview_artist = self.ax_kline.plot([], [], color=self.drawing_color.currentData(),
            linewidth=self.drawing_width.value(), linestyle='--', marker='o', markersize=5,
            animated=True, zorder=18)[0]
        self._refresh_preview_artist()

    def _paint_drawing_overlay(self):
        for artists in self._drawing_artists.values():
            for artist in artists:
                if artist.get_animated() and artist.get_visible():
                    self.ax_kline.draw_artist(artist)
        if self._preview_artist is not None and self._preview_artist.get_visible():
            self.ax_kline.draw_artist(self._preview_artist)
