import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import time
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from PyQt6.QtCore import QDateTime, QPoint, QPointF, Qt, QEvent
from PyQt6.QtGui import QWheelEvent, QMouseEvent, QKeyEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

from gui.main_window import StockDoubleBlindTrainer


class GuiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = StockDoubleBlindTrainer()
        self.window._save_settings = lambda: None
        self.window.cb_t0.setChecked(False)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()

    def wait_for(self, condition):
        deadline = time.monotonic() + 10
        while not condition():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail('GUI operation timed out')
        self.app.processEvents()

    def start_minute_session(self, target_index=0):
        index = pd.date_range('2025-11-21 09:35', periods=120, freq='5min')
        close = np.linspace(20, 22, len(index))
        frame = pd.DataFrame({'Open': close, 'High': close + .1,
                              'Low': close - .1, 'Close': close,
                              'Volume': 1000}, index=index)
        with patch('gui.main_window.QFileDialog.getOpenFileName',
                   return_value=('BJ#920000.txt', '')), patch(
                       'gui.main_window.load_market_data_file',
                       return_value=(frame, '920000', '测试股票', '5min')):
            self.window.import_single_file()
        self.wait_for(lambda: self.window.stock_data is not None
                      and self.window.precompute_thread is None
                      and (self.window.synth_thread is None
                           or not self.window.synth_thread.isRunning()))
        self.window.de_start_date.setDateTime(QDateTime(index[target_index].to_pydatetime()))
        with patch.object(QMessageBox, 'warning') as warning, patch.object(
                QMessageBox, 'critical') as critical:
            self.window.start_training()
            self.wait_for(lambda: self.window.precompute_thread is None)
            warning.assert_not_called()
            critical.assert_not_called()

    def test_local_5min_first_start(self):
        self.start_minute_session()
        self.assertEqual(self.window.current_period, '5min')
        self.assertEqual(self.window.next_idx, 60)
        self.assertEqual(self.window.workspace_stack.currentIndex(), 1)

    def test_local_5min_preserves_exact_start(self):
        self.start_minute_session(target_index=20)
        self.assertEqual(self.window.current_period, '5min')
        self.assertEqual(self.window.next_idx, 20)

    def test_real_tdx_text_import_starts_without_first_row_warning(self):
        w = self.window
        path = os.path.join(os.path.dirname(__file__), 'fixtures', 'BJ#920000.txt')
        with patch('gui.main_window.QFileDialog.getOpenFileName', return_value=(path, '')):
            w.import_single_file()
        self.wait_for(lambda: w.stock_data is not None and w.precompute_thread is None)
        self.assertEqual(w.source_period_key, '5min')
        self.assertEqual(w.imported_stock_code, '920000')
        w.de_start_date.setDateTime(QDateTime(w.imported_data.index[0].to_pydatetime()))
        with patch.object(QMessageBox, 'warning') as warning:
            w.start_training()
            self.wait_for(lambda: w.precompute_thread is None)
            warning.assert_not_called()
        w.next_trading_day()
        self.assertEqual(w.current_date_idx, 5)
        self.assertTrue(w.btn_buy.isEnabled())

    def test_cursor_no_trails_after_resize_and_full_draw(self):
        self.start_minute_session()
        w = self.window
        for width, height in [(1280, 800), (1440, 900), (1120, 720)]:
            w.resize(width, height)
            self.app.processEvents()
            w.canvas.draw()
            for x in range(59):
                w._move_cursor_to_data_x(x, fast=True)
            w._move_cursor_to_data_x(59.49, fast=True)
            self.assertEqual(w.cursor_abs_idx, w.display_end_idx)
            actual = np.asarray(w.canvas.buffer_rgba()).copy()
            # A clean full redraw with the same final cursor must match exactly.
            w.canvas.draw()
            expected = np.asarray(w.canvas.buffer_rgba()).copy()
            np.testing.assert_array_equal(actual, expected)

    def test_trade_validation_markers_and_t1(self):
        self.start_minute_session(target_index=20)
        w = self.window
        w.next_trading_day()
        w.sb_trade_amount.setValue(100)
        with patch.object(QMessageBox, 'warning') as warning:
            w.buy_stock()
            warning.assert_not_called()
        self.assertEqual(w.simulator.get_current_hold(), 100)
        self.assertEqual(len(w.trade_markers), 1)
        with patch.object(QMessageBox, 'warning') as warning:
            w.sell_stock()
            warning.assert_called_once()
        self.assertEqual(w.simulator.get_current_hold(), 100)
        w.le_trade_price.setText('9999')
        with patch.object(QMessageBox, 'warning') as warning:
            w.buy_stock()
            warning.assert_called_once()
        self.assertEqual(len(w.simulator.trade_history), 1)

    def test_save_and_restore_offline_session(self):
        self.start_minute_session(target_index=20)
        w = self.window
        w.next_trading_day()
        w.buy_stock()
        w.set_drawing_mode('horizontal')
        w.add_drawing_point(10, 20.25)
        w.drawing_color.setCurrentIndex(3)
        w.drawing_width.setValue(2.5)
        w.drawing_style.setCurrentIndex(1)
        w.toggle_drawing_lock()
        w.indicator_checks['MACD'].setChecked(True)
        original = w.simulator.to_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'session.json')
            with patch('gui.main_window.QFileDialog.getSaveFileName', return_value=(path, '')):
                w.save_session()
            w.next_trading_day()
            with patch('gui.main_window.QFileDialog.getOpenFileName', return_value=(path, '')), patch.object(
                    QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(
                    QMessageBox, 'warning') as warning:
                w.load_session()
                self.wait_for(lambda: w.precompute_thread is None)
                warning.assert_not_called()
        self.assertEqual(w.current_date_idx, 20)
        self.assertEqual(w.simulator.to_snapshot(), original)
        self.assertEqual(w.current_period, '5min')
        self.assertEqual(len(w.drawings), 1)
        self.assertEqual(w.drawings[0]['color'], '#FFD166')
        self.assertEqual(w.drawings[0]['width'], 2.5)
        self.assertEqual(w.drawings[0]['style'], '--')
        self.assertTrue(w.drawings[0]['locked'])
        self.assertEqual(w._drawing_undo, [])
        self.assertTrue(w.indicator_checks['MACD'].isChecked())
        self.assertEqual(len(w.trade_markers), 1)

    def test_indicator_and_playback_controls(self):
        self.start_minute_session()
        w = self.window
        w.indicator_checks['成交量'].setChecked(False)
        w.indicator_checks['MACD'].setChecked(False)
        self.assertFalse(w.ax_volume.get_visible())
        self.assertFalse(w.ax_macd.get_visible())
        w.indicator_checks['MACD'].setChecked(True)
        self.assertTrue(w.ax_macd.get_visible())
        w.toggle_playback()
        self.assertTrue(w.play_timer.isActive())
        w.toggle_playback()
        self.assertFalse(w.play_timer.isActive())
        w.set_drawing_mode('trend')
        w.add_drawing_point(1, 20)
        w.add_drawing_point(10, 21)
        self.assertEqual(len(w.drawings), 1)
        w.clear_drawings()
        self.assertEqual(w.drawings, [])

    def switch_period(self, period):
        self.window.on_period_button_clicked(period)
        self.wait_for(lambda: self.window.current_period == period
                      and self.window.precompute_thread is None
                      and not self.window.synth_thread.isRunning())

    def test_period_switch_preserves_account_and_reveals_only_past(self):
        self.start_minute_session(target_index=20)
        w = self.window
        w.next_trading_day()
        w.buy_stock()
        original = w.simulator.to_snapshot()
        cutoff = w.current_datetime
        self.switch_period('20min')
        raw = w.raw_min_data
        boundary = w.stock_data.index[w.current_date_idx]
        prefix = raw[(raw.index > boundary - pd.Timedelta(minutes=20)) & (raw.index <= cutoff)]
        self.assertAlmostEqual(w._get_current_price(), prefix.Close.iloc[-1])
        self.assertEqual(w.current_datetime, cutoff)
        self.assertTrue(w.btn_buy.isEnabled())
        self.switch_period('5min')
        self.assertEqual(w.simulator.to_snapshot(), original)
        self.assertEqual(w.current_datetime, cutoff)
        self.assertEqual(w.current_date_idx, 20)
        self.switch_period('D')
        self.assertEqual(w.history_end_idx, -1)
        self.assertEqual(len(w.trade_markers), 1)
        self.assertAlmostEqual(w._get_current_price(), raw.loc[:cutoff].Close.iloc[-1])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'coarse.json')
            with patch('gui.main_window.QFileDialog.getSaveFileName', return_value=(path, '')):
                w.save_session()
            with patch('gui.main_window.QFileDialog.getOpenFileName', return_value=(path, '')), patch.object(
                    QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(
                    QMessageBox, 'warning') as warning:
                w.load_session()
                self.wait_for(lambda: w.precompute_thread is None)
                warning.assert_not_called()
        self.assertEqual(w.current_datetime, cutoff)
        self.assertEqual(w.simulator.to_snapshot(), original)
        self.assertEqual(len(w.trade_markers), 1)
        self.assertAlmostEqual(w._get_current_price(), raw.loc[:cutoff].Close.iloc[-1])

    def test_mouse_wheel_drag_and_price_cursor(self):
        self.start_minute_session()
        w = self.window
        canvas = w.canvas
        center = QPointF(canvas.width() / 2, canvas.height() / 3)
        original = w.display_end_idx - w.display_start_idx
        wheel = QWheelEvent(center, center, QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        canvas.wheelEvent(wheel)
        self.assertLess(w.display_end_idx - w.display_start_idx, original)
        press = QMouseEvent(QEvent.Type.MouseButtonPress, center, center,
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        canvas.mousePressEvent(press)
        self.assertIsNotNone(w._cursor_price)
        start = w.display_start_idx
        moved = center + QPointF(120, 0)
        canvas.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, moved, moved,
                              Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier))
        self.assertLess(w.display_start_idx, start)
        canvas.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, moved, moved,
                                 Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                                 Qt.KeyboardModifier.NoModifier))
        self.assertIsNone(canvas._drag_x)

    def drawing_mouse(self, kind, x, price, held=False):
        canvas = self.window.canvas
        pixels = self.window.ax_kline.transData.transform((x, price))
        point = QPointF(pixels[0] / canvas.device_pixel_ratio,
                        (canvas.figure.bbox.height - pixels[1]) / canvas.device_pixel_ratio)
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
        buttons = Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton
        event = QMouseEvent(kind, point, point, button, buttons, Qt.KeyboardModifier.NoModifier)
        {QEvent.Type.MouseButtonPress: canvas.mousePressEvent,
         QEvent.Type.MouseMove: canvas.mouseMoveEvent,
         QEvent.Type.MouseButtonRelease: canvas.mouseReleaseEvent}[kind](event)

    def test_drawing_preview_blits_and_escape_cancels_anchor(self):
        self.start_minute_session()
        w = self.window
        w.set_drawing_mode('trend')
        self.drawing_mouse(QEvent.Type.MouseButtonPress, 5, 20.2, True)
        self.drawing_mouse(QEvent.Type.MouseButtonRelease, 5, 20.2)
        self.assertIsNotNone(w._trend_anchor)
        with patch.object(w, '_draw_combined_chart') as redraw:
            self.drawing_mouse(QEvent.Type.MouseMove, 15, 20.4)
            self.drawing_mouse(QEvent.Type.MouseMove, 20, 20.5)
            redraw.assert_not_called()
        self.assertEqual(len(w.drawings), 0)
        self.assertEqual(list(w._preview_artist.get_xdata()), [5, 20])
        w.canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                         Qt.KeyboardModifier.NoModifier))
        self.assertIsNone(w._trend_anchor)
        self.assertEqual(w.drawing_mode, 'cursor')
        self.assertFalse(w._preview_artist.get_visible())
        self.drawing_mouse(QEvent.Type.MouseButtonPress, 15, 20.4, True)
        self.drawing_mouse(QEvent.Type.MouseButtonRelease, 15, 20.4)
        self.assertEqual(w.drawings, [])

    def test_drawing_endpoint_and_whole_line_drag_with_undo(self):
        self.start_minute_session()
        w = self.window
        w.set_drawing_mode('trend')
        w.add_drawing_point(5, 20.2)
        w.add_drawing_point(15, 20.4)
        w.set_drawing_mode('cursor')
        self.drawing_mouse(QEvent.Type.MouseButtonPress, 5, 20.2, True)
        self.assertIsNotNone(w._drawing_drag)
        with patch.object(w, '_draw_combined_chart') as redraw:
            self.drawing_mouse(QEvent.Type.MouseMove, 7, 20.25, True)
            redraw.assert_not_called()
        self.drawing_mouse(QEvent.Type.MouseButtonRelease, 7, 20.25)
        self.assertEqual(w.drawings[0]['points'][0][0], w.stock_data.index[7].isoformat())
        self.assertAlmostEqual(w.drawings[0]['points'][0][1], 20.25)
        self.drawing_mouse(QEvent.Type.MouseButtonPress, 11, 20.325, True)
        self.assertIsNone(w._drawing_drag['endpoint'])
        self.drawing_mouse(QEvent.Type.MouseMove, 14, 20.425, True)
        self.drawing_mouse(QEvent.Type.MouseButtonRelease, 14, 20.425)
        self.assertEqual(w.drawings[0]['points'][0][0], w.stock_data.index[10].isoformat())
        self.assertEqual(w.drawings[0]['points'][1][0], w.stock_data.index[18].isoformat())
        self.assertAlmostEqual(w.drawings[0]['points'][0][1], 20.35)
        w.undo_drawing()
        self.assertEqual(w.drawings[0]['points'][0][0], w.stock_data.index[7].isoformat())
        w.redo_drawing()
        self.assertEqual(w.drawings[0]['points'][0][0], w.stock_data.index[10].isoformat())

    def test_horizontal_selection_lock_delete_clear_and_history(self):
        self.start_minute_session()
        w = self.window
        w.set_drawing_mode('horizontal')
        w.add_drawing_point(5, 20.3)
        w.set_drawing_mode('cursor')
        self.drawing_mouse(QEvent.Type.MouseButtonPress, 20, 20.3, True)
        self.drawing_mouse(QEvent.Type.MouseButtonRelease, 20, 20.4)
        self.assertAlmostEqual(w.drawings[0]['points'][0][1], 20.4)
        w.drawing_color.setCurrentIndex(3)
        w.drawing_style.setCurrentIndex(1)
        self.assertEqual(w.drawings[0]['color'], '#FFD166')
        self.assertEqual(w.drawings[0]['style'], '--')
        w.toggle_drawing_lock()
        self.drawing_mouse(QEvent.Type.MouseButtonPress, 20, 20.4, True)
        self.assertIsNone(w._drawing_drag)
        w.delete_selected_drawing()
        self.assertEqual(len(w.drawings), 1)
        w.toggle_drawing_lock()
        w.canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete,
                                         Qt.KeyboardModifier.NoModifier))
        self.assertEqual(w.drawings, [])
        w.canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z,
                                         Qt.KeyboardModifier.ControlModifier))
        self.assertEqual(len(w.drawings), 1)
        w.clear_drawings()
        w.undo_drawing()
        self.assertEqual(len(w.drawings), 1)
        w.redo_drawing()
        self.assertEqual(w.drawings, [])

    def test_escape_restores_drag_and_hidden_drawings_cannot_be_picked(self):
        self.start_minute_session()
        w = self.window
        w.set_drawing_mode('trend')
        w.add_drawing_point(5, 20.2)
        w.add_drawing_point(15, 20.4)
        original = [point[:] for point in w.drawings[0]['points']]
        w.set_drawing_mode('cursor')
        self.drawing_mouse(QEvent.Type.MouseButtonPress, 5, 20.2, True)
        self.drawing_mouse(QEvent.Type.MouseMove, 8, 20.3, True)
        w.exit_cursor_mode()
        self.assertEqual(w.drawings[0]['points'], original)
        self.assertIsNone(w._drawing_drag)
        w.show_drawings.setChecked(False)
        self.assertFalse(w.begin_drawing_drag(5, 20.2))
        self.assertEqual(w._drawing_artists, {})
        w.show_drawings.setChecked(True)
        self.assertIn(0, w._drawing_artists)

    def test_drawing_new_session_resets_undo_and_preview(self):
        self.start_minute_session()
        w = self.window
        w.set_drawing_mode('horizontal')
        w.add_drawing_point(5, 20.3)
        w.set_drawing_mode('trend')
        w.add_drawing_point(5, 20.2)
        w.start_training()
        self.wait_for(lambda: w.precompute_thread is None)
        self.assertEqual(w.drawings, [])
        self.assertEqual(w._drawing_undo, [])
        self.assertIsNone(w._trend_anchor)
        self.assertEqual(w.drawing_mode, 'cursor')

    def test_preview_and_drag_no_trails_after_resize(self):
        self.start_minute_session()
        w = self.window
        w.set_drawing_mode('trend')
        w.add_drawing_point(5, 20.2)
        for size in [(1280, 800), (1120, 720)]:
            w.resize(*size)
            self.app.processEvents()
            for x in range(6, 30):
                w.update_drawing_preview((x, 20.3 + x * .005))
            actual = np.asarray(w.canvas.buffer_rgba()).copy()
            w.canvas.draw()
            np.testing.assert_array_equal(actual, np.asarray(w.canvas.buffer_rgba()))
        w.add_drawing_point(15, 20.4)
        w.set_drawing_mode('cursor')
        self.drawing_mouse(QEvent.Type.MouseButtonPress, 5, 20.2, True)
        for x in range(6, 25):
            self.drawing_mouse(QEvent.Type.MouseMove, x, 20.3 + x * .005, True)
        actual = np.asarray(w.canvas.buffer_rgba()).copy()
        w.canvas.draw()
        np.testing.assert_array_equal(actual, np.asarray(w.canvas.buffer_rgba()))
        self.drawing_mouse(QEvent.Type.MouseButtonRelease, 24, 20.42)

    def test_legacy_drawing_archive_uses_default_style(self):
        self.start_minute_session()
        w = self.window
        w.set_drawing_mode('horizontal')
        w.add_drawing_point(5, 20.3)
        for key in ('color', 'width', 'style'):
            w.drawings[0].pop(key)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'legacy.json')
            with patch('gui.main_window.QFileDialog.getSaveFileName', return_value=(path, '')):
                w.save_session()
            with patch('gui.main_window.QFileDialog.getOpenFileName', return_value=(path, '')), patch.object(
                    QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(
                    QMessageBox, 'warning') as warning:
                w.load_session()
                self.wait_for(lambda: w.precompute_thread is None)
                warning.assert_not_called()
        self.assertEqual(w._drawing_artists[0][0].get_color(), '#639CFF')
        self.assertEqual(w._drawing_artists[0][0].get_linewidth(), 1.3)
        self.assertEqual(w._drawing_artists[0][0].get_linestyle(), '-')

    def test_finished_session_disables_all_trading_controls(self):
        self.start_minute_session()
        w = self.window
        w.next_trading_day()
        with patch('gui.main_window.QDialog.exec', return_value=0):
            w.finish_session()
        w._update_trade_buttons_state()
        for button in (w.btn_buy, w.btn_sell, w.btn_next, w.btn_play):
            self.assertFalse(button.isEnabled())
        index = w.current_date_idx
        w.next_trading_day()
        self.assertEqual(w.current_date_idx, index)

    def test_ctrl_wheel_keeps_mouse_anchor_in_main_and_volume_panels(self):
        self.start_minute_session()
        w = self.window
        for axis in (w.ax_kline, w.ax_volume):
            w.display_start_idx, w.display_end_idx = 5, 54
            w._draw_combined_chart()
            pixels = (axis.bbox.x0 + axis.bbox.width * .25, axis.bbox.y0 + axis.bbox.height * .5)
            point = QPointF(pixels[0] / w.canvas.device_pixel_ratio,
                            (w.fig.bbox.height - pixels[1]) / w.canvas.device_pixel_ratio)
            before = w.display_start_idx + axis.transData.inverted().transform(pixels)[0]
            w.canvas.wheelEvent(QWheelEvent(point, point, QPoint(), QPoint(0, 120),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                                Qt.ScrollPhase.NoScrollPhase, False))
            after = w.display_start_idx + axis.transData.inverted().transform(pixels)[0]
            self.assertAlmostEqual(before, after, delta=.51)
            self.assertEqual(w.display_end_idx - w.display_start_idx + 1, 40)
            self.assertLessEqual(w.display_end_idx, w.current_date_idx)

    def test_arrow_keys_always_pan_chart_without_moving_cursor(self):
        self.start_minute_session()
        w = self.window
        w._move_cursor_to_index(20, fast=True, price=22.)
        w.display_start_idx, w.display_end_idx = 10, 40
        w._draw_combined_chart()
        w.canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left,
                                        Qt.KeyboardModifier.NoModifier))
        self.assertEqual((w.display_start_idx, w.display_end_idx), (9, 39))
        self.assertEqual(w.cursor_abs_idx, 20)
        self.assertEqual(w._cursor_price, 22.)
        w.canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right,
                                        Qt.KeyboardModifier.NoModifier))
        self.assertEqual(w.cursor_abs_idx, 20)
        self.assertEqual((w.display_start_idx, w.display_end_idx), (10, 40))
        w.exit_cursor_mode()
        w.canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left,
                                        Qt.KeyboardModifier.NoModifier))
        self.assertEqual((w.display_start_idx, w.display_end_idx), (9, 39))
        w.canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right,
                                        Qt.KeyboardModifier.NoModifier))
        self.assertEqual((w.display_start_idx, w.display_end_idx), (10, 40))
        w.display_start_idx, w.display_end_idx = 29, w.current_date_idx
        w._draw_combined_chart()
        w.canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right,
                                        Qt.KeyboardModifier.NoModifier))
        self.assertEqual(w.display_end_idx, w.current_date_idx)

    def test_zoom_short_history_never_extends_beyond_revealed_data(self):
        self.start_minute_session(target_index=3)
        w = self.window
        w._zoom(1.2, anchor_x=1)
        self.assertLessEqual(w.display_end_idx, w.current_date_idx)
        w._zoom(.8, anchor_x=1)
        self.assertGreaterEqual(w.display_start_idx, 0)
        self.assertLessEqual(w.display_end_idx, w.current_date_idx)


if __name__ == '__main__':
    unittest.main()
