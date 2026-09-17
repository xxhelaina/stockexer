"""Start-triggered local lookup, with explicit consent for online fetching."""
import os

import pandas as pd
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from gui.auto_data_thread import AutoDataThread
from training_data import PROJECT_DIR, range_covered
from utils import validate_stock_code
from app_paths import local_search_roots


class AutoDataMixin:
    def _init_auto_data(self):
        self._auto_data_thread = None
        self._auto_result = None
        self._auto_ready_request = None
        self._auto_start_request = None
        self._auto_allow_online = False
        self._auto_missing = None
        self._auto_closing = False
        self._auto_search_roots = local_search_roots() + list(self.settings.value('local_data_roots', [], type=list))
        self._auto_file_paths = []
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(800)
        self._auto_timer.timeout.connect(self._prepare_auto_data)
        for signal in (self.cb_random_stock.toggled, self.cb_specific_stock.toggled,
                       self.le_stock_code.textChanged, self.de_start_date.dateTimeChanged,
                       self.de_end_date.dateTimeChanged, self.cb_training_period.currentIndexChanged):
            signal.connect(self._schedule_auto_data)
        self.btn_auto_cancel.clicked.connect(self.cancel_auto_data)

    def _remember_data_root(self, path, directory=False):
        root = os.path.abspath(path if directory else os.path.dirname(path))
        if root not in self._auto_search_roots:
            self._auto_search_roots.append(root)
        if not directory and os.path.abspath(path) not in self._auto_file_paths:
            self._auto_file_paths.append(os.path.abspath(path))
        self._auto_ready_request = None

    def _auto_request(self):
        random_mode = self.cb_random_stock.isChecked()
        code = self.le_stock_code.text().strip()
        if not random_mode and (not self.cb_specific_stock.isChecked() or not validate_stock_code(code)):
            raise ValueError('请选择随机股票，或输入正确的6位股票代码')
        start = self.de_start_date.dateTime().toPyDateTime()
        end = self.de_end_date.dateTime().toPyDateTime()
        if end <= start:
            raise ValueError('结束时间必须晚于开始时间')
        return {'random': random_mode, 'code': None if random_mode else code,
                'start': start, 'end': end, 'period': self.cb_training_period.currentData()}

    def _schedule_auto_data(self, *_):
        if self.workspace_stack.currentWidget() != self.setup_page or self._auto_closing:
            return
        self._auto_start_request = None
        self._auto_allow_online = False
        self._auto_missing = None
        self._auto_timer.stop()
        self.btn_start.setEnabled(True)
        self._auto_ready_request = None
        self._auto_result = None
        if self._auto_data_thread is not None:
            self._auto_data_thread.requestInterruption()
        try:
            self._auto_request()
        except ValueError:
            self._auto_timer.stop()
            return
        self.lbl_source_status.setText('● 参数已设置 · 点击开始训练检索本地数据')

    def _auto_memory(self):
        if self.imported_data is None:
            return None
        return (self.imported_data, self.imported_stock_code, self.imported_stock_name,
                self.source_period_key, '当前已导入数据')

    def _memory_auto_result(self, request):
        # No disk or network on the GUI thread. This fast path also avoids an
        # asynchronous round trip for a manually imported, explicitly chosen stock.
        memory = self._auto_memory()
        if memory is None or request['random'] or memory[1] != request['code']:
            return None
        raw, code, name, source_period, origin = memory
        if request['period'] not in ('auto', source_period):
            return None
        frame, period = raw, source_period
        if not range_covered(frame, request['start'], request['end'], period):
            return None
        limit = pd.Timestamp(request['end'])
        if not period.endswith('min'):
            limit = limit.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        frame = frame.loc[:limit].copy()
        if len(frame) < 3:
            return None
        return frame, {'code': code, 'name': name, 'period': period,
            'source_period': source_period, 'raw': raw.loc[:limit].copy(), 'origin': origin}

    def _prepare_auto_data(self):
        if self._auto_closing or self._auto_start_request is None:
            return
        if any(thread is not None and thread.isRunning() for thread in
               (self.precompute_thread, self.synth_thread, self.scan_thread)):
            self._auto_timer.start()
            return
        try:
            request = self._auto_request()
        except ValueError:
            return
        if request != self._auto_start_request:
            return
        if self._auto_data_thread is not None:
            if self._auto_data_thread.request != request:
                self._auto_data_thread.requestInterruption()
                self._auto_timer.start()
            return
        if self._auto_ready_request == request:
            if self._auto_start_request == request:
                self._start_resolved_training(*self._auto_result, request)
            return
        memory_result = self._memory_auto_result(request)
        if memory_result is not None:
            self._receive_auto_data(request, *memory_result)
            return
        paths = self._auto_file_paths + [item[0] for item in self.raw_stock_files.values()]
        thread = AutoDataThread(request, self._auto_search_roots, paths, self._auto_memory(),
                                allow_online=self._auto_allow_online)
        self._auto_data_thread = thread
        thread.progress.connect(lambda text: self._auto_progress(thread, text))
        thread.resolved.connect(lambda frame, meta: self._receive_auto_data(thread.request, frame, meta)
                                if not thread.isInterruptionRequested() else None)
        thread.failed.connect(lambda error: self._auto_failed(thread, error))
        thread.missing.connect(lambda error: self._local_data_missing(thread, error))
        thread.finished.connect(lambda: self._auto_stopped(thread))
        self.btn_auto_cancel.show()
        self.lbl_source_status.setText('● 正在检索本地行情…')
        thread.start()

    def _auto_progress(self, thread, text):
        if not thread.isInterruptionRequested():
            self.lbl_source_status.setText(f'● {text}')
            self.statusBar().showMessage(text)

    def _receive_auto_data(self, request, frame, meta):
        try:
            current_request = self._auto_request()
        except ValueError:
            return
        if request != current_request or self._auto_closing:
            return
        self._auto_ready_request = request
        self._auto_result = (frame, meta)
        self.lbl_source_status.setText(f'● 已就绪 · {meta["code"]} · {meta["period"]}')
        self.lbl_source_status.setStyleSheet('color: #10B981; font-weight: 600;')
        self.lbl_data_path.setText(f'股票：{meta["name"]}({meta["code"]})\n来源：{meta["origin"]}\n'
            f'共 {len(frame):,} 根K线（包含起点前历史）\n范围：{frame.index[0]} 至 {frame.index[-1]}')
        self.statusBar().showMessage('行情已匹配所选股票、周期和时间范围', 10000)
        if self._auto_start_request == request:
            self._start_resolved_training(frame, meta, request)

    def _auto_failed(self, thread, error):
        if thread.isInterruptionRequested():
            return
        self.lbl_source_status.setText('● 数据准备失败')
        self.lbl_source_status.setStyleSheet('color: #F59E0B; font-weight: 600;')
        self.lbl_data_path.setText(error)
        if self._auto_start_request == thread.request:
            self._auto_start_request = None
            self._auto_allow_online = False
            self.btn_start.setEnabled(True)
            QMessageBox.warning(self, '数据准备失败', error)

    def _auto_stopped(self, thread):
        interrupted = thread.isInterruptionRequested()
        if self._auto_data_thread is thread:
            self._auto_data_thread = None
            self.btn_auto_cancel.hide()
        # Keep Qt ownership until the native run() has actually returned.
        thread.deleteLater()
        if self._auto_closing:
            self.close()
            return
        missing = self._auto_missing
        if missing is not None and missing[0] == thread.request:
            self._auto_missing = None
            if not interrupted and self._auto_start_request == missing[0]:
                self._choose_missing_data_source(*missing)
        elif interrupted and self._auto_start_request is not None:
            QTimer.singleShot(0, self._prepare_auto_data)

    def _local_data_missing(self, thread, error):
        if not thread.isInterruptionRequested() and self._auto_start_request == thread.request:
            self._auto_missing = (thread.request, error)

    def _ask_missing_data_source(self, error):
        box = QMessageBox(self)
        box.setWindowTitle('缺少本地行情数据')
        box.setIcon(QMessageBox.Icon.Question)
        box.setText('本地数据不足，请选择获取方式')
        box.setInformativeText(error)
        local = box.addButton('本地导入', QMessageBox.ButtonRole.ActionRole)
        online = box.addButton('网上获取', QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(local)
        box.setEscapeButton(cancel)
        box.exec()
        return 'local' if box.clickedButton() is local else 'online' if box.clickedButton() is online else 'cancel'

    def _choose_missing_data_source(self, request, error):
        choice = self._ask_missing_data_source(error)
        if self._auto_closing or self._auto_start_request != request:
            return
        if choice == 'local':
            folder = QFileDialog.getExistingDirectory(self, '选择本地行情文件夹')
            if self._auto_start_request != request or self._auto_closing:
                return
            if not folder:
                self.cancel_auto_data()
                return
            self._remember_data_root(folder, directory=True)
            self.lbl_tdx_folder.setText(f'本地数据目录：{folder}')
            self._auto_allow_online = False
        elif choice == 'online':
            self._auto_allow_online = True
        else:
            self.cancel_auto_data()
            return
        # Preserve the stock, interval and period chosen before starting.
        self._prepare_auto_data()

    def cancel_auto_data(self):
        self._auto_timer.stop()
        self._auto_start_request = None
        self._auto_allow_online = False
        self._auto_missing = None
        self.btn_start.setEnabled(True)
        if self._auto_data_thread is not None:
            self._auto_data_thread.requestInterruption()
        self.lbl_source_status.setText('● 已取消数据准备')

    def _start_resolved_training(self, frame, meta, request):
        self._auto_timer.stop()
        self._auto_start_request = None
        self._auto_allow_online = False
        self.stop_playback()
        self._exit_fenshi_mode()
        self.imported_data = meta['raw']
        self.imported_stock_code = meta['code']
        self.imported_stock_name = meta['name']
        self.source_period_key = meta['source_period']
        self.is_min_data = self.source_period_key.endswith('min')
        self.raw_min_data = meta['raw'] if self.is_min_data else None
        self.current_period = meta['period']
        self.stock_data_raw = frame
        self.period_data_cache = {self.source_period_key: meta['raw'], self.current_period: frame}
        self._training_end_datetime = request['end']
        self._user_target_datetime = request['start']
        self._user_exact_target = request['start'] if self.is_min_data else None
        self._user_target_pending = False
        self._set_checked_period(self.current_period)
        self._update_fenshi_button_state()
        self.lbl_cur_stock.setText(f'{meta["name"]} {meta["code"]}')
        self.btn_clear_data.setEnabled(True)
        for key, button in self.period_buttons.items():
            if key.endswith('min'):
                button.setEnabled(self.is_min_data and int(key[:-3]) % int(self.source_period_key[:-3]) == 0)
        self._set_trade_buttons_enabled(False)
        self.btn_start.setEnabled(False)
        self._precompute_indicators_async()
