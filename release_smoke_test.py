"""Opt-in frozen-EXE smoke test; never runs during normal GUI startup."""
import json
import multiprocessing
import os
import sys
import time
import traceback
from pathlib import Path


def _spawn_probe(connection):
    try:
        connection.send({'frozen': bool(getattr(sys, 'frozen', False)), 'pid': os.getpid()})
    finally:
        connection.close()


def run(report_path, network=False):
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'ok': False, 'frozen': bool(getattr(sys, 'frozen', False)), 'checks': {}}
    window = None
    try:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        import tempfile
        import numpy as np
        import pandas as pd
        from unittest.mock import patch
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtGui import QFont, QFontDatabase
        from PyQt6.QtCore import QDateTime, Qt
        from app_paths import application_dir, data_dir, download_dir
        from gui.main_window import StockDoubleBlindTrainer

        report['application_dir'] = str(application_dir())
        report['data_dir'] = str(data_dir())
        report['download_dir'] = str(download_dir())
        app = QApplication.instance() or QApplication([])
        app.setStyle('Fusion')
        font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'msyh.ttc'
        if not QFontDatabase.families() and font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont('Microsoft YaHei UI', 9))

        def wait(condition):
            deadline = time.monotonic() + 15
            while not condition():
                app.processEvents()
                if time.monotonic() > deadline:
                    raise TimeoutError('GUI preparation exceeded 15 seconds')
                time.sleep(.002)
            app.processEvents()

        context = multiprocessing.get_context('spawn')
        reader, writer = context.Pipe(duplex=False)
        worker = context.Process(target=_spawn_probe, args=(writer,))
        try:
            worker.start()
            writer.close()
            if not reader.poll(10):
                raise TimeoutError('Frozen multiprocessing child did not respond')
            probe = reader.recv()
            assert probe['pid'] != os.getpid()
            assert probe['frozen'] == report['frozen']
            report['checks']['multiprocessing'] = probe
        finally:
            reader.close()
            writer.close()
            if worker.pid:
                worker.join(2)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(2)

        indices = []
        for day in pd.bdate_range('2025-11-19', periods=3):
            for hour, minute in [(9, 35), (13, 5)]:
                indices.extend(pd.date_range(day + pd.Timedelta(hours=hour, minutes=minute), periods=24, freq='5min'))
        index = pd.DatetimeIndex(indices)
        price = 20 + np.sin(np.arange(len(index)) / 10)
        opening = price + np.where(np.arange(len(index)) % 2, .05, -.05)
        frame = pd.DataFrame({'Open': opening, 'High': np.maximum(price, opening) + .1,
            'Low': np.minimum(price, opening) - .1, 'Close': price, 'Volume': 1000}, index=index)
        window = StockDoubleBlindTrainer()
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window._save_settings = lambda: None
        window.resize(1120, 720)
        window.show()
        app.processEvents()
        with tempfile.TemporaryDirectory(prefix='stocklab-smoke-') as folder:
            path = Path(folder) / '600000_5min.csv'
            frame.to_csv(path)
            window._auto_search_roots = [folder]
            window.cb_specific_stock.setChecked(True)
            window.le_stock_code.setText('600000')
            window.cb_training_period.setCurrentIndex(window.cb_training_period.findData('5min'))
            window.de_start_date.setDateTime(QDateTime(index[120].to_pydatetime()))
            window.de_end_date.setDateTime(QDateTime(index[-1].to_pydatetime()))
            window.le_initial_capital.setText('1000000')
            window.le_fee_rate.setText('0.003')
            window.cb_hide_date.setChecked(False)
            window.cb_hide_stock.setChecked(False)
            with patch.object(QMessageBox, 'warning') as warning, patch.object(QMessageBox, 'critical') as critical:
                window.start_training()
                wait(lambda: window._auto_data_thread is None and window.precompute_thread is None
                     and window.workspace_stack.currentIndex() == 1)
                assert not warning.called and not critical.called
            assert window.current_period == '5min'
            assert window.ax_volume.get_visible() and window.ax_macd.get_visible()
            assert all(name in window.stock_data for name in ('MA120', 'MA250', 'VOLMA5', 'VOLMA10'))
            window.next_trading_day()
            window._move_cursor_to_index(100, fast=True)
            screenshot = report_path.with_suffix('.png')
            assert window.grab().save(str(screenshot))
            report['checks']['local_5min_replay'] = {'rows': len(window.stock_data),
                'canvas_size': [window.canvas.width(), window.canvas.height()], 'screenshot': str(screenshot)}

        # Verify a network result can be persisted and rediscovered in the bundle's data directory.
        if network:
            from online_data import download_kline
            from training_data import resolve_training_data
            day = pd.Timestamp.now().normalize() - pd.Timedelta(days=1)
            while day.weekday() >= 5:
                day -= pd.Timedelta(days=1)
            start, end = day + pd.Timedelta(hours=10), day + pd.Timedelta(hours=15)
            data, name, source = download_kline('600000', '5min', day.strftime('%Y-%m-%d'),
                day.strftime('%Y-%m-%d'), required_range=(start, end))
            local, meta = resolve_training_data({'random': False, 'code': '600000', 'period': '5min',
                'start': start, 'end': end}, [str(download_dir())])
            assert len(local) > 0
            report['checks']['online_5min_and_cache'] = {'rows': len(data), 'source': source,
                'cache_origin': meta['origin']}
        report['ok'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        if window is not None:
            window.cancel_auto_data()
            for thread in (window._auto_data_thread, window.precompute_thread, window.synth_thread, window.scan_thread):
                if thread is not None and thread.isRunning():
                    thread.requestInterruption()
                    thread.wait(5000)
            window.close()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1
