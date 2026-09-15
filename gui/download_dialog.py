# -*- coding: utf-8 -*-
"""在线行情对话框：BaoStock/东方财富主源，新浪/腾讯自动兜底。"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QComboBox, QDateEdit, QCheckBox, QPushButton,
    QMessageBox, QGroupBox, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QDate

from gui.download_thread import DownloadThread
from utils import validate_stock_code


class DownloadDialog(QDialog):
    PERIODS = [
        ('1min', '1分钟'),
        ('5min', '5分钟'),
        ('15min', '15分钟'),
        ('30min', '30分钟'),
        ('60min', '60分钟'),
        ('D', '日线'),
        ('W', '周线'),
        ('M', '月线'),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('在线行情 · 多源自动切换')
        self.setMinimumWidth(460)
        self.thread = None

        # 结果（exec() 返回 Accepted 后由主窗口读取）
        self.result_df = None
        self.result_name = ''
        self.result_source = ''
        self.period_key = '5min'
        self.code = ''

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        grid = QGridLayout()
        grid.addWidget(QLabel('股票代码：'), 0, 0)
        self.le_code = QLineEdit()
        self.le_code.setPlaceholderText('6位数字，如 600000')
        self.le_code.setMaxLength(6)
        grid.addWidget(self.le_code, 0, 1)

        grid.addWidget(QLabel('K线周期：'), 1, 0)
        self.cb_period = QComboBox()
        for key, label in self.PERIODS:
            self.cb_period.addItem(label, key)
        self.cb_period.setCurrentIndex(1)  # 默认5分钟
        grid.addWidget(self.cb_period, 1, 1)

        grid.addWidget(QLabel('开始日期：'), 2, 0)
        self.de_beg = QDateEdit(QDate.currentDate().addMonths(-6))
        self.de_beg.setCalendarPopup(True)
        self.de_beg.setDisplayFormat('yyyy-MM-dd')
        grid.addWidget(self.de_beg, 2, 1)

        grid.addWidget(QLabel('结束日期：'), 3, 0)
        self.de_end = QDateEdit(QDate.currentDate())
        self.de_end.setCalendarPopup(True)
        self.de_end.setDisplayFormat('yyyy-MM-dd')
        grid.addWidget(self.de_end, 3, 1)

        self.cb_fqt = QCheckBox('前复权（仅东财主源支持；兜底源为不复权）')
        self.cb_fqt.setChecked(True)
        grid.addWidget(self.cb_fqt, 4, 0, 1, 2)
        layout.addLayout(grid)

        self.lbl_hint = QLabel(
            '数据源顺序：BaoStock → 东方财富 → 新浪 → 腾讯。\n'
            '5/15/30/60分钟及日线优先使用 BaoStock 长历史；免费1分钟数据范围较短。'
            '下载后会自动缓存为 CSV，实际范围以完成信息为准。')
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet('color: gray; font-size: 9px;')
        layout.addWidget(self.lbl_hint)

        self.lbl_status = QLabel('')
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet('font-size: 9px;')
        layout.addWidget(self.lbl_status)

        btn_box = QDialogButtonBox()
        self.btn_download = QPushButton('开始下载')
        self.btn_cancel = QPushButton('取消')
        btn_box.addButton(self.btn_download, QDialogButtonBox.ButtonRole.AcceptRole)
        btn_box.addButton(self.btn_cancel, QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_download.clicked.connect(self._start_download)
        self.btn_cancel.clicked.connect(self.reject)
        layout.addWidget(btn_box)

    def _start_download(self):
        code = self.le_code.text().strip()
        if not validate_stock_code(code):
            QMessageBox.warning(self, '错误', '请输入正确的6位数字股票代码！')
            return
        if self.de_beg.date() > self.de_end.date():
            QMessageBox.warning(self, '错误', '开始日期不能晚于结束日期！')
            return

        self.period_key = self.cb_period.currentData()
        self.code = code
        self.le_code.setEnabled(False)
        self.cb_period.setEnabled(False)
        self.de_beg.setEnabled(False)
        self.de_end.setEnabled(False)
        self.cb_fqt.setEnabled(False)
        self.btn_download.setEnabled(False)
        self.lbl_status.setText('正在连接免费行情源并校验数据，请稍候…')

        self.thread = DownloadThread(
            code, self.period_key,
            self.de_beg.date().toString('yyyy-MM-dd'),
            self.de_end.date().toString('yyyy-MM-dd'),
            fqt=1 if self.cb_fqt.isChecked() else 0,
        )
        self.thread.finished.connect(self._on_finished)
        self.thread.error.connect(self._on_error)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _on_finished(self, df, meta):
        self.result_df = df
        self.result_name = meta['name']
        self.result_source = meta['source']
        self.lbl_status.setText(
            f'下载成功：{self.result_name}（来源：{self.result_source}）\n'
            f'共 {meta["rows"]} 根K线，范围 {meta["first"]} ~ {meta["last"]}')
        self.lbl_status.setStyleSheet('color: green; font-size: 9px;')
        self.btn_download.setEnabled(True)
        self.btn_download.setText('完成，返回主界面')
        self.btn_download.clicked.disconnect()
        self.btn_download.clicked.connect(self.accept)

    def _on_error(self, msg):
        self.lbl_status.setText(msg)
        self.lbl_status.setStyleSheet('color: red; font-size: 9px;')
        self.le_code.setEnabled(True)
        self.cb_period.setEnabled(True)
        self.de_beg.setEnabled(True)
        self.de_end.setEnabled(True)
        self.cb_fqt.setEnabled(True)
        self.btn_download.setEnabled(True)
