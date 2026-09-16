import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import time
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from PyQt6.QtCore import QDateTime, QPoint, QPointF, Qt, QEvent
from PyQt6.QtGui import QWheelEvent, QMouseEvent
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


if __name__ == '__main__':
    unittest.main()
