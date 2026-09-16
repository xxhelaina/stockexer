# -*- coding: utf-8 -*-
"""主窗口类（修复：分钟数据识别，周期切换，开始按钮重置）"""
import sys
import random
import pandas as pd
import numpy as np
import os
import re
from datetime import datetime, date
from typing import Optional, Dict, List, Any, Tuple
import logging
import json

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QButtonGroup, QCheckBox,
    QPushButton, QDateEdit, QDateTimeEdit, QSpinBox, QTableWidget, QTableWidgetItem,
    QGroupBox, QGridLayout, QMessageBox, QHeaderView, QSizePolicy,
    QComboBox, QFileDialog, QProgressDialog, QDialog, QScrollArea, QFrame,
    QAbstractItemView, QStackedWidget, QAbstractSpinBox
)
from PyQt6.QtCore import Qt, QDate, QDateTime, QSettings, QTimer
from PyQt6.QtGui import QDoubleValidator, QColor

import matplotlib
from matplotlib.figure import Figure
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import Rectangle

from models import TradingSimulator
from data_loader import (
    is_valid_kline_file, load_stock_names, load_stock_data_file,
    parse_tdx_day_file, load_tdx_text_file, load_market_data_file
)
from gui.chart_canvas import MyFigureCanvas
from gui.download_dialog import DownloadDialog
from gui.scan_thread import ScanFolderThread
from gui.precompute_thread import PrecomputeIndicatorsThread
from gui.synthesize_thread import SynthesizePeriodThread
import config
from utils import logger, validate_stock_code

matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
matplotlib.use('QtAgg')


class StockDoubleBlindTrainer(QMainWindow):
    ZOOM_STEP = config.ZOOM_STEP
    MIN_DISPLAY_COUNT = config.MIN_DISPLAY_COUNT
    # 分钟级周期（分时图仅对这些周期可用）
    MINUTE_PERIOD_KEYS = ('1min', '5min', '15min', '20min', '30min', '60min')

    def __init__(self):
        super().__init__()
        self.simulator = TradingSimulator()
        self.stock_data: Optional[pd.DataFrame] = None
        self.stock_data_raw: Optional[pd.DataFrame] = None
        self.trading_days: List[datetime] = []
        self.current_date_idx: int = -1
        self.current_date: Optional[datetime] = None
        self.history_end_idx: int = -1
        self.next_idx: int = -1

        # 多周期支持
        self.raw_min_data: Optional[pd.DataFrame] = None
        self.is_min_data: bool = False               # 新增：标记是否存在分钟数据
        self.source_period_key: str = 'D'
        self.period_data_cache: Dict[str, pd.DataFrame] = {}
        self.current_period: str = 'D'
        self.current_datetime: Optional[datetime] = None
        self.history_end_datetime: Optional[datetime] = None
        self._pending_period_switch: Optional[Dict] = None
        self.period_buttons: Dict[str, QPushButton] = {}

        # 分时图/K线图视图切换（训练中可随时切换）
        self.view_mode: str = 'kline'                      # 'kline' | 'fenshi'
        self._kline_win_backup: Optional[Tuple[int, int]] = None

        # 用户选择的精确训练起始时刻（分钟级数据专用，日线周期只按日期定位）
        self._user_exact_target: Optional[datetime] = None
        self._user_target_pending: bool = False

        self.imported_data: Optional[pd.DataFrame] = None
        self.imported_stock_name: str = ""
        self.imported_stock_code: str = ""

        self.raw_stock_files: Dict[str, Tuple[str, Optional[str], str]] = {}
        self.stock_names: Dict[str, str] = {}

        self.display_start_idx: int = 0
        self.display_end_idx: int = -1

        self.settings = QSettings("YourCompany", "StockTrainer")
        self._load_settings()

        # 绘图集合
        self.kline_lines = None
        self.kline_rects = None
        self.ma_lines = {}
        self.macd_lines = {}
        self.macd_bars = None
        self.volume_bars = None
        self.open_lines = []
        self.close_lines = []
        # 光标使用 Matplotlib blit 单独刷新，避免鼠标移动时重建整张图。
        self.cursor_artists: Dict[str, Any] = {}
        self._cursor_backgrounds: Dict[Any, Any] = {}

        # 光标模式
        self.cursor_mode = False
        self.cursor_abs_idx = -1
        self._cursor_price = None
        self.drawing_mode = 'cursor'
        self.drawings = []
        self._trend_anchor = None
        self.session_finished = False
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self.next_trading_day)

        # 线程
        self.scan_thread: Optional[ScanFolderThread] = None
        self.precompute_thread: Optional[PrecomputeIndicatorsThread] = None
        self.synth_thread: Optional[SynthesizePeriodThread] = None
        self.progress_dialog: Optional[QProgressDialog] = None

        self.initUI()

    # ----------------- 初始化 UI -----------------
    def _load_settings(self):
        self.saved_initial_capital = self.settings.value("initial_capital", config.DEFAULT_INITIAL_CAPITAL, type=float)
        self.saved_fee_rate = self.settings.value("fee_rate", config.DEFAULT_FEE_RATE, type=float)
        self.saved_hide_date = self.settings.value("hide_date", False, type=bool)
        self.saved_hide_stock = self.settings.value("hide_stock", False, type=bool)
        self.saved_t0 = self.settings.value("t0", False, type=bool)
        self.saved_short = self.settings.value("short", False, type=bool)

    def _save_settings(self):
        self.settings.setValue("initial_capital", float(self.le_initial_capital.text()))
        self.settings.setValue("fee_rate", float(self.le_fee_rate.text()))
        self.settings.setValue("hide_date", self.cb_hide_date.isChecked())
        self.settings.setValue("hide_stock", self.cb_hide_stock.isChecked())
        self.settings.setValue("t0", self.cb_t0.isChecked())
        self.settings.setValue("short", self.cb_short.isChecked())

    def initUI(self):
        self.setWindowTitle("StockLab · A股复盘训练")
        self.setGeometry(80, 60, 1440, 900)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #0B0E11; color: #D7DCE2; font-size: 12px; }
            QLabel, QCheckBox { background: transparent; }
            QGroupBox {
                font-weight: 600; border: 1px solid #252B32; border-radius: 8px;
                margin-top: 10px; padding: 13px 10px 10px 10px; background: #11151A;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #A8B0BA;
            }
            QPushButton {
                min-height: 30px; font-weight: 600; border: 1px solid #30363D;
                border-radius: 6px; padding: 0 10px; background: #1A1F26; color: #E6E9ED;
            }
            QPushButton:hover { background: #232A32; border-color: #525B66; }
            QPushButton:pressed { background: #12161B; }
            QPushButton:disabled { color: #535B65; background: #14181D; border-color: #23282E; }
            QPushButton#primaryBtn, QPushButton#startBtn { background: #F0F3F6; border-color: #F0F3F6; color: #111418; }
            QPushButton#primaryBtn:hover, QPushButton#startBtn:hover { background: #FFFFFF; }
            QPushButton#startBtn { min-height: 42px; font-size: 14px; }
            QPushButton#dangerBtn, QPushButton#resetBtn { background: transparent; color: #E16B75; border-color: #63333A; }
            QPushButton#buyBtn { background: #00B786; border-color: #00C894; color: white; }
            QPushButton#sellBtn { background: #F52D62; border-color: #FF4676; color: white; }
            QPushButton#nextBtn { background: #E8ECF1; border-color: #E8ECF1; color: #111418; min-height: 38px; }
            QPushButton#periodBtn { min-height: 25px; max-height: 25px; padding: 0 8px; border: none; background: transparent; color: #7F8791; }
            QPushButton#periodBtn:hover { background: #1A2027; color: #DCE1E7; }
            QPushButton#periodBtn:checked { background: #272E36; color: #FFFFFF; }
            QPushButton#stepBtn { min-width: 32px; max-width: 38px; padding: 0; }
            QLineEdit, QSpinBox, QComboBox, QDateEdit, QDateTimeEdit {
                background: #0D1116; color: #E6E9ED; border: 1px solid #30363D;
                border-radius: 5px; min-height: 28px; padding: 0 7px; selection-background-color: #3A424C;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus { border-color: #68727E; }
            QCheckBox { spacing: 7px; color: #C2C8CF; }
            QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #46505C; border-radius: 3px; background: #0D1116; }
            QCheckBox::indicator:checked { background: #00A87B; border-color: #00B786; }
            QCheckBox::indicator:disabled { background: #14181D; border-color: #252B32; }
            QLabel#mutedLabel { color: #747D87; }
            QLabel#dataBadge { color: #A5ADB7; background: #0D1116; border: 1px solid #252B32; border-radius: 6px; padding: 10px; }
            QLabel#sourceBadge { color: #D8A64B; font-weight: 600; }
            QLabel#brandLabel { color: #F8FAFC; font-size: 17px; font-weight: 700; }
            QLabel#symbolLabel { color: #F8FAFC; font-size: 14px; font-weight: 700; }
            QLabel#timeBadge { color: #AAB2BC; background: #1A1F26; border-radius: 5px; padding: 5px 9px; }
            QLabel#quoteBar { color: #8E97A2; background: transparent; padding: 3px 2px; }
            QLabel#metricHero { color: #F8FAFC; font-size: 22px; font-weight: 700; }
            QLabel#metricValue { color: #E2E8F0; font-weight: 600; }
            QLabel#pageTitle { color: #F4F6F8; font-size: 24px; font-weight: 700; }
            QLabel#sectionTitle { color: #E9EDF1; font-size: 13px; font-weight: 700; }
            QFrame#topBar { background: #0F1317; border-bottom: 1px solid #22272E; }
            QFrame#setupCard { background: #11151A; border: 1px solid #252B32; border-radius: 12px; }
            QFrame#chartCard, QFrame#tradeBar, QFrame#historyCard { background: #11151A; border: 1px solid #252B32; border-radius: 8px; }
            QTableWidget { background: #0D1116; alternate-background-color: #10151B; border: none; gridline-color: #20262D; }
            QHeaderView::section { background: #171C22; color: #8F98A3; border: none; border-right: 1px solid #252B32; padding: 6px; }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: #0B0E11; width: 9px; }
            QScrollBar::handle:vertical { background: #343B44; border-radius: 4px; min-height: 30px; }
            QStatusBar { background: #090C0F; color: #6F7883; border-top: 1px solid #20252B; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self._init_top_bar(root_layout)

        self.workspace_stack = QStackedWidget()
        root_layout.addWidget(self.workspace_stack, 1)
        self._init_setup_workspace()
        self._init_trading_workspace()

        self._set_trade_buttons_enabled(False)
        self._set_session_mode(False)
        self.statusBar().showMessage("就绪 · 请选择在线行情或本地数据开始复盘")

    def _init_setup_workspace(self):
        """开局页：先完成数据与训练参数，再进入交易工作区。"""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(22, 18, 22, 18)
        page_layout.addStretch(1)

        center_row = QHBoxLayout()
        center_row.addStretch(1)
        card = QFrame()
        card.setObjectName('setupCard')
        card.setMinimumWidth(880)
        card.setMaximumWidth(940)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 21, 24, 22)
        card_layout.setSpacing(10)

        title = QLabel('开始一次复盘训练')
        title.setObjectName('pageTitle')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title)
        subtitle = QLabel('载入历史行情，选择一个起点，然后逐根推进 K 线检验交易决策。')
        subtitle.setObjectName('mutedLabel')
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(3)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        left = QVBoxLayout()
        right = QVBoxLayout()
        self._init_data_source_config(left)
        self._init_date_config(right)
        self._init_base_config(right)
        self._init_rule_config(right)
        columns.addLayout(left, 5)
        columns.addLayout(right, 4)
        card_layout.addLayout(columns)
        self._init_action_buttons(card_layout)

        center_row.addWidget(card, 0)
        center_row.addStretch(1)
        page_layout.addLayout(center_row)
        page_layout.addStretch(1)
        self.workspace_stack.addWidget(page)
        self.setup_page = page

    def _init_trading_workspace(self):
        """训练页：图表、交易控制和账户表现集中在同一屏。"""
        page = QWidget()
        main_layout = QHBoxLayout(page)
        main_layout.setContentsMargins(9, 8, 9, 7)
        main_layout.setSpacing(8)

        left_main_layout = QVBoxLayout()
        left_main_layout.setSpacing(7)
        main_layout.addLayout(left_main_layout, 1)
        self._init_chart_panel(left_main_layout)

        side = QVBoxLayout()
        side.setSpacing(8)
        self.btn_end_session = QPushButton('结束本轮')
        self.btn_end_session.setObjectName('resetBtn')
        self.btn_end_session.clicked.connect(self.finish_session)
        side.addWidget(self.btn_end_session)
        self._init_trade_panel(side)
        self._init_status_panel(side)
        self._init_history_panel_new(side)
        self.btn_new_session = QPushButton('新一轮训练')
        self.btn_new_session.clicked.connect(lambda: self.reset_training(keep_data=True))
        side.addWidget(self.btn_new_session)
        side_widget = QWidget()
        side_widget.setLayout(side)
        self.trading_sidebar = QScrollArea()
        self.trading_sidebar.setWidgetResizable(True)
        self.trading_sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.trading_sidebar.setFixedWidth(380)
        self.trading_sidebar.setWidget(side_widget)
        main_layout.addWidget(self.trading_sidebar)

        self.workspace_stack.addWidget(page)
        self.trading_page = page

    def _init_top_bar(self, parent_layout: QVBoxLayout):
        top_bar = QFrame()
        top_bar.setObjectName('topBar')
        top_bar.setFixedHeight(50)
        layout = QHBoxLayout(top_bar)
        layout.setContentsMargins(15, 5, 13, 5)

        brand = QLabel('STOCKLAB')
        brand.setObjectName('brandLabel')
        layout.addWidget(brand)
        subtitle = QLabel('历史行情回放训练')
        subtitle.setObjectName('mutedLabel')
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        self.lbl_cur_stock = QLabel('等待载入行情')
        self.lbl_cur_stock.setObjectName('symbolLabel')
        layout.addWidget(self.lbl_cur_stock)
        layout.addStretch()
        self.lbl_header_period = QLabel('日线')
        self.lbl_header_period.setObjectName('timeBadge')
        layout.addWidget(self.lbl_header_period)
        self.lbl_cur_date = QLabel('未开始')
        self.lbl_cur_date.setObjectName('timeBadge')
        layout.addWidget(self.lbl_cur_date)
        self.btn_save_session = QPushButton('保存训练')
        self.btn_save_session.clicked.connect(self.save_session)
        layout.addWidget(self.btn_save_session)
        self.btn_load_session = QPushButton('恢复训练')
        self.btn_load_session.clicked.connect(self.load_session)
        layout.addWidget(self.btn_load_session)
        parent_layout.addWidget(top_bar)

    def _init_chart_panel(self, parent_layout: QVBoxLayout):
        chart_group = QFrame()
        chart_group.setObjectName('chartCard')
        chart_layout = QVBoxLayout(chart_group)
        chart_layout.setContentsMargins(9, 8, 9, 7)
        chart_layout.setSpacing(5)

        heading_row = QHBoxLayout()
        heading = QLabel('价格走势')
        heading.setObjectName('sectionTitle')
        heading_row.addWidget(heading)
        heading_row.addStretch()
        self.lbl_chart_quote = QLabel('O --   H --   L --   C --   VOL --')
        self.lbl_chart_quote.setObjectName('quoteBar')
        heading_row.addWidget(self.lbl_chart_quote)
        chart_layout.addLayout(heading_row)

        period_layout = QHBoxLayout()
        period_layout.setSpacing(2)
        periods = ['1分钟', '5分钟', '15分钟', '20分钟', '30分钟', '60分钟', '日线', '周线', '月线']
        period_keys = ['1min', '5min', '15min', '20min', '30min', '60min', 'D', 'W', 'M']
        for text, key in zip(periods, period_keys):
            btn = QPushButton(text)
            btn.setObjectName('periodBtn')
            btn.setCheckable(True)
            btn.setFixedHeight(25)
            btn.clicked.connect(lambda checked, k=key: self.on_period_button_clicked(k))
            period_layout.addWidget(btn)
            self.period_buttons[key] = btn

        # 分时图切换按钮（训练中可随时切换，仅分钟级周期可用）
        self.btn_fenshi = QPushButton('分时图')
        self.btn_fenshi.setObjectName('periodBtn')
        self.btn_fenshi.setCheckable(True)
        self.btn_fenshi.setFixedHeight(25)
        self.btn_fenshi.setEnabled(False)
        self.btn_fenshi.clicked.connect(self.toggle_fenshi_view)
        period_layout.addSpacing(6)
        period_layout.addWidget(self.btn_fenshi)
        period_layout.addStretch()
        chart_layout.addLayout(period_layout)
        tools = QHBoxLayout()
        self.indicator_checks = {}
        for name, checked in [('MA', True), ('成交量', True), ('MACD', False)]:
            check = QCheckBox(name)
            check.setChecked(checked)
            check.toggled.connect(lambda _: self._draw_combined_chart())
            self.indicator_checks[name] = check
            tools.addWidget(check)
        tools.addSpacing(10)
        self.drawing_buttons = {}
        for title, mode in [('十字光标', 'cursor'), ('水平线', 'horizontal'), ('趋势线', 'trend')]:
            button = QPushButton(title)
            button.setCheckable(True)
            button.setObjectName('periodBtn')
            button.clicked.connect(lambda _, m=mode: self.set_drawing_mode(m))
            tools.addWidget(button)
            self.drawing_buttons[mode] = button
        self.drawing_buttons['cursor'].setChecked(True)
        clear = QPushButton('清除画线')
        clear.clicked.connect(self.clear_drawings)
        tools.addWidget(clear)
        tools.addStretch()
        shot = QPushButton('截图')
        shot.clicked.connect(self.export_chart)
        tools.addWidget(shot)
        full = QPushButton('全屏')
        full.clicked.connect(lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
        tools.addWidget(full)
        chart_layout.addLayout(tools)
        self.period_buttons['D'].setChecked(True)

        self.fig = Figure(figsize=(12, 7), dpi=100)
        self.canvas = MyFigureCanvas(self.fig, self)
        self.canvas.mpl_connect('draw_event', self._on_chart_draw)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setMinimumHeight(280)
        chart_layout.addWidget(self.canvas)

        self.fig.clear()
        self.fig.patch.set_facecolor('#0D1116')
        gs = self.fig.add_gridspec(3, 1, height_ratios=[3, 1, 1], hspace=0.02)
        self.ax_kline = self.fig.add_subplot(gs[0, 0])
        self.ax_volume = self.fig.add_subplot(gs[1, 0], sharex=self.ax_kline)
        self.ax_macd = self.fig.add_subplot(gs[2, 0], sharex=self.ax_kline)
        self.ax_kline.set_visible(False)
        self.ax_volume.set_visible(False)
        self.ax_macd.set_visible(False)

        parent_layout.addWidget(chart_group)

    def toggle_playback(self):
        if self.play_timer.isActive():
            self.stop_playback()
        elif self._is_training_active_silent() and not self.session_finished:
            self.update_play_speed()
            self.play_timer.start()
            self.btn_play.setText('暂停回放')

    def stop_playback(self):
        self.play_timer.stop()
        self.btn_play.setText('自动回放')

    def update_play_speed(self, *_):
        self.play_timer.setInterval([500, 1000, 2000][self.play_speed.currentIndex()])

    def finish_session(self):
        if not self._is_training_active_silent():
            return
        self.stop_playback()
        self.session_finished = True
        self._set_trade_buttons_enabled(False)
        self.btn_play.setEnabled(False)
        dialog = QDialog(self)
        dialog.setWindowTitle('本轮训练结算')
        layout = QVBoxLayout(dialog)
        equity = self.simulator.get_total_asset(self._get_current_price())
        initial = float(self.simulator.initial_capital)
        layout.addWidget(QLabel(
            f'总权益：¥ {equity:,.2f}\n总收益：{equity - initial:+,.2f} '
            f'({(equity / initial - 1) * 100:+.2f}%)\n'
            f'已实现：{self.simulator.get_realized_pnl():+,.2f}\n'
            f'未实现：{self.simulator.get_unrealized_pnl(self._get_current_price()):+,.2f}\n'
            f'成交笔数：{len(self.simulator.trade_history)}\n'
            '结算按当前市价估值，不自动平仓。'))
        save = QPushButton('保存本轮训练')
        save.clicked.connect(self.save_session)
        layout.addWidget(save)
        close = QPushButton('返回查看图表')
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def set_drawing_mode(self, mode):
        self.drawing_mode = mode
        self._trend_anchor = None
        for key, button in self.drawing_buttons.items():
            button.setChecked(key == mode)
        self.cursor_mode = mode == 'cursor'
        self._cursor_price = None
        self._draw_combined_chart()

    def add_drawing_point(self, x_data, price):
        idx = max(self.display_start_idx, min(self.display_end_idx,
                  self.display_start_idx + int(np.floor(x_data + .5))))
        point = [self.stock_data.index[idx].isoformat(), float(price)]
        if self.drawing_mode == 'horizontal':
            self.drawings.append({'period': self.current_period, 'type': 'horizontal', 'points': [point]})
        elif self.drawing_mode == 'trend':
            if self._trend_anchor is None:
                self._trend_anchor = point
                self.statusBar().showMessage('趋势线：请点击第二个端点', 5000)
                return
            self.drawings.append({'period': self.current_period, 'type': 'trend',
                                  'points': [self._trend_anchor, point]})
            self._trend_anchor = None
        self._draw_combined_chart()

    def clear_drawings(self):
        self.drawings = []
        self._trend_anchor = None
        self._draw_combined_chart()

    def _draw_annotations(self, start_idx, end_idx):
        self.trade_markers = []
        for record in self.simulator.trade_history:
            label = self._dt_to_period_label(record.date, self.current_period)
            idx = int(self.stock_data.index.searchsorted(label))
            if start_idx <= idx <= end_idx:
                buy = record.action.value == '买入'
                marker = self.ax_kline.scatter(idx - start_idx, record.price,
                    marker='^' if buy else 'v', color='#FF6471' if buy else '#00BC8A',
                    s=65, edgecolors='#FFFFFF', linewidths=.5, zorder=12)
                self.trade_markers.append(marker)
        for drawing in self.drawings:
            if drawing['period'] != self.current_period:
                continue
            if drawing['type'] == 'horizontal':
                self.ax_kline.axhline(drawing['points'][0][1], color='#639CFF', linewidth=1)
            else:
                points = drawing['points']
                xs = [int(self.stock_data.index.searchsorted(pd.Timestamp(p[0]))) - start_idx for p in points]
                self.ax_kline.plot(xs, [p[1] for p in points], color='#639CFF', linewidth=1.3)

    def export_chart(self):
        if not self._is_training_active_silent():
            return
        path, _ = QFileDialog.getSaveFileName(self, '保存图表截图', 'StockLab.png', 'PNG 图片 (*.png)')
        if path:
            try:
                self.fig.savefig(path, dpi=self.fig.dpi, facecolor=self.fig.get_facecolor())
                self.statusBar().showMessage('图表截图已保存', 5000)
            except Exception as error:
                QMessageBox.warning(self, '保存失败', str(error))

    @staticmethod
    def _pack_frame(frame):
        columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        return {'times': [timestamp.isoformat() for timestamp in frame.index],
                'rows': frame[columns].astype(float).values.tolist()}

    @staticmethod
    def _unpack_frame(data):
        frame = pd.DataFrame(data['rows'], columns=['Open', 'High', 'Low', 'Close', 'Volume'],
                             index=pd.to_datetime(data['times']))
        if len(frame) < 1 or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError('存档行情时间索引错误')
        if frame.index.hasnans or not np.isfinite(frame.values).all():
            raise ValueError('存档行情包含无效数值')
        if (frame[['Open', 'High', 'Low', 'Close']] <= 0).any().any():
            raise ValueError('存档行情价格必须大于0')
        if ((frame.High < frame[['Open', 'Close', 'Low']].max(axis=1)) |
            (frame.Low > frame[['Open', 'Close', 'High']].min(axis=1)) | (frame.Volume < 0)).any():
            raise ValueError('存档行情OHLC关系错误')
        return frame

    def save_session(self):
        if not self._is_training_active_silent():
            QMessageBox.information(self, '尚未开始', '请先开始训练，再保存进度。')
            return
        self.stop_playback()
        path, _ = QFileDialog.getSaveFileName(self, '保存训练存档', 'StockLab-session.json', 'JSON 存档 (*.json)')
        if not path:
            return
        try:
            payload = {'version': 1, 'data': self._pack_frame(self.stock_data_raw),
                'raw': self._pack_frame(self.raw_min_data if self.is_min_data else self.imported_data),
                'period': self.current_period, 'source_period': self.source_period_key,
                'name': self.imported_stock_name, 'code': self.imported_stock_code,
                'current': self.current_date_idx, 'history_end': self.history_end_idx,
                'cutoff': str(self.current_datetime), 'history_cutoff': str(self.history_end_datetime),
                'display': [self.display_start_idx, self.display_end_idx],
                'finished': self.session_finished, 'drawings': self.drawings,
                'hide_date': self.cb_hide_date.isChecked(), 'hide_stock': self.cb_hide_stock.isChecked(),
                'indicators': {key: value.isChecked() for key, value in self.indicator_checks.items()},
                'quantity': self.sb_trade_amount.value(), 'ratio': self.ratio_combo.currentIndex(),
                'speed': self.play_speed.currentIndex(), 'view_mode': self.view_mode,
                'simulator': self.simulator.to_snapshot()}
            with open(path, 'w', encoding='utf-8') as file:
                json.dump(payload, file, ensure_ascii=False, allow_nan=False)
            self.statusBar().showMessage('训练已保存，存档包含行情，可在另一台电脑离线恢复', 7000)
        except Exception as error:
            QMessageBox.warning(self, '保存失败', str(error))

    def load_session(self):
        path, _ = QFileDialog.getOpenFileName(self, '恢复训练存档', '', 'JSON 存档 (*.json)')
        if not path:
            return
        try:
            if os.path.getsize(path) > 100 * 1024 * 1024:
                raise ValueError('存档超过100MB，请使用较短的行情区间')
            with open(path, encoding='utf-8') as file:
                payload = json.load(file)
            if payload['version'] != 1:
                raise ValueError('不支持的存档版本')
            data = self._unpack_frame(payload['data'])
            raw = self._unpack_frame(payload['raw'])
            if len(raw) < 3:
                raise ValueError('存档原始行情不足3根K线')
            simulator = TradingSimulator.from_snapshot(payload['simulator'])
            if not (-1 <= payload['history_end'] <= payload['current'] < len(data)):
                raise ValueError('存档进度超出行情范围')
            cutoff = pd.Timestamp(payload.get('cutoff', data.index[payload['current']]))
            history_cutoff = pd.Timestamp(payload.get('history_cutoff', data.index[max(0, payload['history_end'])]))
            if pd.isna(cutoff) or pd.isna(history_cutoff) or history_cutoff > cutoff:
                raise ValueError('存档时间边界错误')
            left, right = payload['display']
            if not (0 <= left <= right <= payload['current']):
                raise ValueError('存档图表范围错误')
            if payload['period'] not in self.period_buttons or payload['source_period'] not in self.period_buttons:
                raise ValueError('存档周期错误')
            if payload['code'] != simulator.current_stock:
                raise ValueError('存档股票不一致')
            for key in ('finished', 'hide_date', 'hide_stock'):
                if not isinstance(payload[key], bool):
                    raise ValueError('存档状态格式错误')
            if not isinstance(payload['indicators'], dict) or any(
                    not isinstance(payload['indicators'].get(key), bool) for key in self.indicator_checks):
                raise ValueError('存档指标配置错误')
            if not 1 <= payload.get('quantity', 100) <= 1000000 or not 0 <= payload.get('ratio', 0) < 5:
                raise ValueError('存档数量/仓位错误')
            if payload.get('speed', 1) not in (0, 1, 2) or payload.get('view_mode', 'kline') not in ('kline', 'fenshi'):
                raise ValueError('存档回放参数错误')
            if any(record.date > cutoff for record in simulator.trade_history):
                raise ValueError('存档成交超出了当前已揭示的行情')
            for drawing in payload['drawings']:
                if drawing['period'] not in self.period_buttons or drawing['type'] not in ('horizontal', 'trend'):
                    raise ValueError('存档画线类型错误')
                if len(drawing['points']) != (1 if drawing['type'] == 'horizontal' else 2):
                    raise ValueError('存档画线端点错误')
                for timestamp, price in drawing['points']:
                    pd.Timestamp(timestamp)
                    if not np.isfinite(float(price)):
                        raise ValueError('存档画线价格错误')
            if self._is_training_active_silent():
                reply = QMessageBox.question(self, '恢复训练', '恢复存档会替换当前训练，是否继续？')
                if reply != QMessageBox.StandardButton.Yes:
                    return
            if any(thread is not None and thread.isRunning() for thread in
                   (self.precompute_thread, self.synth_thread, self.scan_thread)):
                raise ValueError('请等待当前数据处理完成后恢复')
            self.stop_playback()
            self._exit_fenshi_mode()
            self.stock_data_raw = data
            self.imported_data = raw
            self.source_period_key = payload['source_period']
            self.is_min_data = self.source_period_key.endswith('min')
            self.raw_min_data = raw if self.is_min_data else None
            self.period_data_cache = {self.source_period_key: raw, payload['period']: data}
            self.current_period = payload['period']
            self.imported_stock_name = payload['name']
            self.imported_stock_code = payload['code']
            self.simulator = simulator
            self.le_initial_capital.setText(str(simulator.initial_capital))
            self.le_fee_rate.setText(str(simulator.fee_rate))
            self.cb_t0.setChecked(simulator.allow_t0)
            self.cb_hide_date.setChecked(payload['hide_date'])
            self.cb_hide_stock.setChecked(payload['hide_stock'])
            self._restore_payload = payload
            self._precompute_indicators_async()
        except Exception as error:
            QMessageBox.warning(self, '恢复失败', str(error))

    def _complete_restore(self, payload):
        self.current_date_idx = payload['current']
        self.history_end_idx = payload['history_end']
        self.next_idx = self.current_date_idx + 1
        self.current_date = pd.Timestamp(payload.get('cutoff', self.trading_days[self.current_date_idx]))
        self.current_datetime = self.current_date
        self.history_end_datetime = pd.Timestamp(payload.get('history_cutoff', self.trading_days[max(0, self.history_end_idx)]))
        self._apply_revealed_aggregate(self.current_datetime)
        self.display_start_idx, self.display_end_idx = payload['display']
        self.session_finished = payload['finished']
        self.drawings = payload['drawings']
        self.cursor_mode = True
        self.cursor_abs_idx = self.current_date_idx
        self._cursor_price = None
        self.drawing_mode = 'cursor'
        self._trend_anchor = None
        for mode, button in self.drawing_buttons.items():
            button.setChecked(mode == 'cursor')
        self.sb_trade_amount.setValue(payload.get('quantity', 100))
        self.ratio_combo.setCurrentIndex(payload.get('ratio', 0))
        self.play_speed.setCurrentIndex(payload.get('speed', 1))
        self._reset_collections()
        self._set_checked_period(self.current_period)
        self._update_fenshi_button_state()
        if payload.get('view_mode') == 'fenshi' and self.btn_fenshi.isEnabled():
            self.view_mode = 'fenshi'
            self.btn_fenshi.setChecked(True)
            self._clamp_display_to_day()
        for key, check in self.indicator_checks.items():
            check.blockSignals(True)
            check.setChecked(payload['indicators'].get(key, False))
            check.blockSignals(False)
        self._update_data_label('训练存档', self.imported_stock_name, self.imported_stock_code, self.imported_data)
        self._update_status_ui()
        self._update_trade_history()
        self._set_session_mode(True)
        self._update_trade_buttons_state()
        self._draw_combined_chart()
        self.lbl_source_status.setText('● 已恢复离线存档')
        self.statusBar().showMessage('训练进度、持仓、行情和画线已恢复', 6000)

    def _init_right_panel(self, main_layout: QHBoxLayout):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(350)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(7, 0, 7, 8)
        scroll.setWidget(right_panel)
        main_layout.addWidget(scroll, 1)

        self._init_status_panel(right_layout)
        self._init_data_source_config(right_layout)
        self._init_date_config(right_layout)
        self._init_base_config(right_layout)
        self._init_rule_config(right_layout)
        self._init_action_buttons(right_layout)
        right_layout.addStretch()

    def _init_base_config(self, parent_layout: QVBoxLayout):
        base_group = QGroupBox("资金设置")
        self.base_group = base_group
        base_grid = QGridLayout(base_group)
        base_grid.addWidget(QLabel("初始资金："), 0, 0)
        self.le_initial_capital = QLineEdit(str(self.saved_initial_capital))
        self.le_initial_capital.setMinimumWidth(120)
        self.le_initial_capital.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.le_initial_capital.setValidator(QDoubleValidator(0, 1e9, 2))
        base_grid.addWidget(self.le_initial_capital, 0, 1)
        base_grid.addWidget(QLabel("元"), 0, 2)

        base_grid.addWidget(QLabel("费率："), 1, 0)
        self.le_fee_rate = QLineEdit(str(self.saved_fee_rate))
        self.le_fee_rate.setMinimumWidth(120)
        self.le_fee_rate.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.le_fee_rate.setValidator(QDoubleValidator(0, 1, 4))
        base_grid.addWidget(self.le_fee_rate, 1, 1)
        base_grid.addWidget(QLabel("（如3‰填0.003）"), 1, 2)

        parent_layout.addWidget(base_group)

    def _init_date_config(self, parent_layout: QVBoxLayout):
        date_group = QGroupBox("回放起点")
        self.date_group = date_group
        date_grid = QGridLayout(date_group)
        date_grid.addWidget(QLabel("开始时间："), 0, 0)
        self.de_start_date = QDateTimeEdit(QDateTime.currentDateTime().addMonths(-12))
        self.de_start_date.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.de_start_date.setCalendarPopup(True)
        date_grid.addWidget(self.de_start_date, 0, 1, 1, 2)

        self.btn_random_start = QPushButton("随机片段")
        self.btn_random_start.setToolTip("随机选择一个至少保留60根历史K线的训练起点")
        self.btn_random_start.clicked.connect(self._choose_random_start)
        date_grid.addWidget(self.btn_random_start, 1, 1, 1, 2)

        info_label = QLabel("起点后的行情会隐藏，点击“下一根”逐步揭示。")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-size: 9px;")
        date_grid.addWidget(info_label, 2, 0, 1, 3)

        parent_layout.addWidget(date_group)

    def _choose_random_start(self):
        df = self.raw_min_data if self.is_min_data and self.raw_min_data is not None else self.imported_data
        if df is None or len(df) < 3:
            QMessageBox.information(self, "尚无行情", "请先载入在线行情或本地数据。")
            return
        lower = min(60, max(1, len(df) // 3))
        upper = max(lower, len(df) - max(2, len(df) // 10))
        index = random.randint(lower, upper)
        self.de_start_date.setDateTime(QDateTime(df.index[index].to_pydatetime()))
        self.statusBar().showMessage(
            f"已随机选择训练起点 · 前方保留 {index} 根历史K线", 5000)

    def _init_data_source_config(self, parent_layout: QVBoxLayout):
        data_group = QGroupBox("行情数据")
        self.data_group = data_group
        data_layout = QVBoxLayout(data_group)

        self.lbl_data_path = QLabel("当前：未加载数据，请先导入")
        self.lbl_data_path.setWordWrap(True)
        self.lbl_data_path.setObjectName("dataBadge")

        self.lbl_source_status = QLabel("● 尚未载入")
        self.lbl_source_status.setObjectName("sourceBadge")

        self.btn_import_data = QPushButton("导入本地")
        self.btn_import_data.setToolTip("自动识别 CSV/TXT/TSV/DAY 的编码、表头与K线周期")
        self.btn_import_data.clicked.connect(self.import_single_file)

        self.btn_download_online = QPushButton("在线行情")
        self.btn_download_online.setObjectName("primaryBtn")
        self.btn_download_online.setToolTip("从多个免费行情源下载并自动缓存到本地")
        self.btn_download_online.clicked.connect(self.download_online_data)

        self.btn_select_tdx_raw = QPushButton("扫描通达信数据目录")
        self.btn_select_tdx_raw.setToolTip("递归扫描 vipdoc 或通达信导出目录，可随机抽取股票训练")
        self.btn_select_tdx_raw.clicked.connect(self.select_tdx_raw_folder)

        self.lbl_tdx_folder = QLabel("未选择原始数据文件夹")
        self.lbl_tdx_folder.setWordWrap(True)
        self.lbl_tdx_folder.setObjectName("mutedLabel")

        self.cb_random_stock = QCheckBox("随机选择股票")
        self.cb_specific_stock = QCheckBox("根据股票代码选择股票")
        self.stock_selection_group = QButtonGroup(self)
        self.stock_selection_group.addButton(self.cb_random_stock)
        self.stock_selection_group.addButton(self.cb_specific_stock)
        self.stock_selection_group.setExclusive(True)
        self.cb_random_stock.setChecked(True)
        self.le_stock_code = QLineEdit()
        self.le_stock_code.setPlaceholderText("输入6位股票代码")
        self.le_stock_code.setEnabled(False)
        self.cb_specific_stock.toggled.connect(self.le_stock_code.setEnabled)

        self.btn_clear_data = QPushButton("清除当前数据")
        self.btn_clear_data.setObjectName("dangerBtn")
        self.btn_clear_data.clicked.connect(self.clear_imported_data)
        self.btn_clear_data.setEnabled(False)

        data_layout.addWidget(self.lbl_source_status)
        data_layout.addWidget(self.lbl_data_path)
        source_buttons = QHBoxLayout()
        source_buttons.addWidget(self.btn_download_online)
        source_buttons.addWidget(self.btn_import_data)
        data_layout.addLayout(source_buttons)
        data_layout.addWidget(self.btn_select_tdx_raw)
        data_layout.addWidget(self.lbl_tdx_folder)
        data_layout.addWidget(self.cb_random_stock)
        data_layout.addWidget(self.cb_specific_stock)
        data_layout.addWidget(self.le_stock_code)
        data_layout.addWidget(self.btn_clear_data)

        parent_layout.addWidget(data_group)

    def _init_rule_config(self, parent_layout: QVBoxLayout):
        rule_group = QGroupBox("训练规则")
        self.rule_group = rule_group
        rule_layout = QVBoxLayout(rule_group)

        self.cb_hide_date = QCheckBox("隐藏日期/时间")
        self.cb_hide_date.setChecked(self.saved_hide_date)
        self.cb_hide_stock = QCheckBox("隐藏股票名称/代码")
        self.cb_hide_stock.setChecked(self.saved_hide_stock)
        self.cb_t0 = QCheckBox("允许T+0")
        self.cb_t0.setChecked(self.saved_t0)
        self.cb_short = QCheckBox("允许卖空（暂不支持）")
        self.cb_short.setChecked(False)
        self.cb_short.setEnabled(False)

        rule_layout.addWidget(self.cb_hide_date)
        rule_layout.addWidget(self.cb_hide_stock)
        rule_layout.addWidget(self.cb_t0)
        rule_layout.addWidget(self.cb_short)

        parent_layout.addWidget(rule_group)

    def _init_action_buttons(self, parent_layout: QVBoxLayout):
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("开始训练")
        self.btn_start.setObjectName("startBtn")
        self.btn_start.clicked.connect(self.start_training)
        btn_layout.addWidget(self.btn_start)
        parent_layout.addLayout(btn_layout)

    def _init_status_panel(self, parent_layout: QVBoxLayout):
        status_group = QWidget()
        self.status_group = status_group
        outer = QVBoxLayout(status_group)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        holdings = QFrame()
        holdings.setObjectName('chartCard')
        holding_layout = QVBoxLayout(holdings)
        holding_layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel('持仓概览')
        title.setObjectName('sectionTitle')
        holding_layout.addWidget(title)
        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        for row, captions in enumerate([('数量', '成本'), ('市价', '浮动盈亏')]):
            for column, caption in enumerate(captions):
                label = QLabel(caption)
                label.setObjectName('mutedLabel')
                grid.addWidget(label, row * 2, column)
        self.lbl_cur_hold = QLabel('0 股')
        self.lbl_avg_cost = QLabel('--')
        self.lbl_market_price = QLabel('--')
        self.lbl_unrealized = QLabel('0.00 元')
        for label, row, column in [(self.lbl_cur_hold, 1, 0), (self.lbl_avg_cost, 1, 1),
                                  (self.lbl_market_price, 3, 0), (self.lbl_unrealized, 3, 1)]:
            label.setObjectName('metricValue')
            grid.addWidget(label, row, column)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        holding_layout.addLayout(grid)
        outer.addWidget(holdings)
        account = QFrame()
        account.setObjectName('chartCard')
        account_layout = QVBoxLayout(account)
        account_layout.setContentsMargins(16, 14, 16, 14)
        equity_caption = QLabel('总权益')
        equity_caption.setObjectName('mutedLabel')
        account_layout.addWidget(equity_caption)
        self.lbl_total_asset = QLabel("¥ 0.00")
        self.lbl_total_asset.setObjectName('metricHero')
        account_layout.addWidget(self.lbl_total_asset)

        status_layout = QGridLayout()
        status_layout.setHorizontalSpacing(12)
        status_layout.setVerticalSpacing(8)
        status_layout.addWidget(QLabel("可用资金"), 0, 0)
        self.lbl_cur_cash = QLabel("0.00 元")
        self.lbl_cur_cash.setObjectName('metricValue')
        status_layout.addWidget(self.lbl_cur_cash, 0, 1)

        status_layout.addWidget(QLabel("已实现盈亏"), 1, 0)
        self.lbl_realized = QLabel("0.00 元")
        self.lbl_realized.setObjectName('metricValue')
        status_layout.addWidget(self.lbl_realized, 1, 1)

        status_layout.addWidget(QLabel("总收益"), 2, 0)
        self.lbl_profit_loss = QLabel("0.00 元  (0.00%)")
        self.lbl_profit_loss.setObjectName('metricValue')
        status_layout.addWidget(self.lbl_profit_loss, 2, 1)

        status_layout.setColumnStretch(1, 1)
        account_layout.addLayout(status_layout)
        outer.addWidget(account)
        parent_layout.addWidget(status_group)

    def _init_trade_panel(self, parent_layout: QVBoxLayout):
        trade_group = QFrame()
        trade_group.setObjectName('tradeBar')
        trade_layout = QVBoxLayout(trade_group)
        trade_layout.setContentsMargins(12, 12, 12, 12)
        trade_layout.setSpacing(10)
        buttons = QHBoxLayout()
        self.btn_next = QPushButton('下一根 →')
        self.btn_next.setShortcut('F10')
        self.btn_next.clicked.connect(self.next_trading_day)
        buttons.addWidget(self.btn_next)
        self.btn_buy = QPushButton('买入')
        self.btn_buy.setObjectName('buyBtn')
        self.btn_buy.setShortcut('F8')
        self.btn_buy.clicked.connect(self._on_buy_clicked)
        buttons.addWidget(self.btn_buy)
        self.btn_sell = QPushButton('卖出')
        self.btn_sell.setObjectName('sellBtn')
        self.btn_sell.setShortcut('F9')
        self.btn_sell.clicked.connect(self._on_sell_clicked)
        buttons.addWidget(self.btn_sell)
        trade_layout.addLayout(buttons)

        price_row = QHBoxLayout()
        price_row.addWidget(QLabel('成交价'))
        self.le_trade_price = QLineEdit()
        self.le_trade_price.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.le_trade_price.setValidator(QDoubleValidator(0, 10000, 2))
        self.le_trade_price.setToolTip('仅接受当前已揭示K线最高/最低价范围内的模拟成交价')
        price_row.addWidget(self.le_trade_price, 1)
        trade_layout.addLayout(price_row)
        trade_layout.addWidget(QLabel('数量（股） / 仓位'))
        quantity = QHBoxLayout()
        self.btn_qty_minus = QPushButton("−")
        self.btn_qty_minus.setObjectName('stepBtn')
        self.btn_qty_minus.clicked.connect(lambda: self._adjust_trade_amount(-100))
        quantity.addWidget(self.btn_qty_minus)
        self.sb_trade_amount = QSpinBox()
        self.sb_trade_amount.setRange(1, 1000000)
        self.sb_trade_amount.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.sb_trade_amount.setValue(100)
        self.sb_trade_amount.setSingleStep(100)
        self.sb_trade_amount.setMinimumWidth(80)
        quantity.addWidget(self.sb_trade_amount, 1)
        self.btn_qty_plus = QPushButton("+")
        self.btn_qty_plus.setObjectName('stepBtn')
        self.btn_qty_plus.clicked.connect(lambda: self._adjust_trade_amount(100))
        quantity.addWidget(self.btn_qty_plus)
        self.ratio_combo = QComboBox()
        self.ratio_combo.addItems(["手动", "全仓", "1/2仓", "1/3仓", "1/4仓"])
        self.ratio_combo.setCurrentIndex(0)
        quantity.addWidget(self.ratio_combo)
        trade_layout.addLayout(quantity)
        self.lbl_control_position = QLabel("持仓 0 股")
        self.lbl_control_position.setObjectName('mutedLabel')
        trade_layout.addWidget(self.lbl_control_position)
        replay = QHBoxLayout()
        self.btn_play = QPushButton('自动回放')
        self.btn_play.clicked.connect(self.toggle_playback)
        replay.addWidget(self.btn_play)
        self.play_speed = QComboBox()
        self.play_speed.addItems(['0.5秒/根', '1秒/根', '2秒/根'])
        self.play_speed.setCurrentIndex(1)
        self.play_speed.currentIndexChanged.connect(self.update_play_speed)
        replay.addWidget(self.play_speed)
        trade_layout.addLayout(replay)
        parent_layout.addWidget(trade_group)

    def _adjust_trade_amount(self, delta: int):
        self.sb_trade_amount.setValue(max(1, self.sb_trade_amount.value() + delta))

    def _init_history_panel_new(self, parent_layout: QHBoxLayout):
        hist_group = QFrame()
        hist_group.setObjectName('historyCard')
        hist_layout = QVBoxLayout(hist_group)
        hist_layout.setContentsMargins(9, 8, 9, 8)
        history_title = QLabel('成交记录')
        history_title.setObjectName('sectionTitle')
        hist_layout.addWidget(history_title)
        hist_group.setMinimumHeight(210)

        self.tbl_trade_hist = QTableWidget()
        self.tbl_trade_hist.setColumnCount(7)
        self.tbl_trade_hist.setHorizontalHeaderLabels(
            ["时间", "方向", "成交价", "数量", "手续费", "成交净额", "已实现盈亏"]
        )
        self.tbl_trade_hist.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_trade_hist.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_trade_hist.setShowGrid(False)
        self.tbl_trade_hist.verticalHeader().setVisible(False)

        header = self.tbl_trade_hist.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column in range(7):
            self.tbl_trade_hist.setColumnWidth(column, 140 if column == 0 else 70)
        self.tbl_trade_hist.setAlternatingRowColors(True)

        hist_layout.addWidget(self.tbl_trade_hist)
        parent_layout.addWidget(hist_group)

    # ----------------- 数据加载 -----------------
    def select_tdx_raw_folder(self):
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "选择包含 .day 或 .txt 股票数据的文件夹（会递归搜索子文件夹）",
            "",
            QFileDialog.Option.ShowDirsOnly
        )
        if not folder_path:
            return

        self.progress_dialog = QProgressDialog("正在扫描文件夹...", "取消", 0, 100, self)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setValue(0)

        self.scan_thread = ScanFolderThread(folder_path)
        self.scan_thread.progress.connect(self.on_scan_progress)
        self.scan_thread.finished.connect(self.on_scan_finished)
        self.scan_thread.error.connect(self.on_scan_error)
        # 修复(2026-08-31)：不使用 deleteLater——线程 run() 栈未完全退出时
        # 被延迟删除会触发原生崩溃(0xC0000409)，改为 Python GC 回收
        self.progress_dialog.canceled.connect(self.scan_thread.requestInterruption)

        self.scan_thread.start()

    def on_scan_progress(self, current, total):
        self.progress_dialog.setMaximum(total)
        self.progress_dialog.setValue(current)
        self.progress_dialog.setLabelText(f"正在扫描... {current}/{total}")

    def on_scan_finished(self, stock_files, stock_names):
        self.progress_dialog.close()
        self.raw_stock_files = stock_files
        self.stock_names = stock_names
        folder_path = self.scan_thread.folder_path
        self.lbl_tdx_folder.setText(f"原始数据文件夹：{folder_path}\n(找到 {len(stock_files)} 个有效文件)")
        self.lbl_tdx_folder.setStyleSheet("color: blue; font-size: 9px;")
        self.scan_thread = None

        txt_count = sum(1 for v in stock_files.values() if v[2] == 'txt')
        day_count = len(stock_files) - txt_count
        QMessageBox.information(self, "扫描完成",
                                f"找到 {txt_count} 个有效 .txt 文件, {day_count} 个 .day 文件\n"
                                f"已加载 {len(stock_names)} 个股票名称")

        # 注意：不要清空已导入的分钟数据

    def on_scan_error(self, error_msg):
        self.progress_dialog.close()
        QMessageBox.warning(self, "扫描错误", f"扫描文件夹时出错：{error_msg}")
        self.scan_thread = None

    def import_single_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择数据文件", "",
            "行情数据 (*.csv *.txt *.tsv *.day);;CSV (*.csv);;通达信 (*.txt *.day);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            if hasattr(self, '_user_target_datetime'):
                del self._user_target_datetime
            df, code, name, period_key = load_market_data_file(file_path)
            self._exit_fenshi_mode()
            self.imported_data = df
            self.imported_stock_name = name
            self.imported_stock_code = code
            self.source_period_key = period_key
            self.current_period = 'D'
            self._set_checked_period('D')
            self.btn_clear_data.setEnabled(True)
            self.lbl_source_status.setText(f"● 本地文件 · {period_key}")
            self.lbl_source_status.setStyleSheet("color: #10B981; font-weight: 600;")
            self._update_data_label(file_path, name, code, df)

            if period_key.endswith('min'):
                self.raw_min_data = df
                self.is_min_data = True
                self.period_data_cache.clear()
                self.period_data_cache[period_key] = df
                self._update_fenshi_button_state()
                self._synthesize_period_data_async('D', min_data_source=df)
            else:
                self.raw_min_data = None
                self.is_min_data = False
                self.period_data_cache.clear()
                self.period_data_cache['D'] = df
                self.stock_data_raw = df
                self._update_fenshi_button_state()
                self._precompute_indicators_async()
            self.statusBar().showMessage(
                f"已载入 {name}({code}) · {period_key} · {len(df):,} 根K线", 8000)
        except Exception as e:
            logger.error(f"导入文件失败: {e}", exc_info=True)
            QMessageBox.warning(
                self, "导入失败",
                f"无法读取 {os.path.basename(file_path)}\n\n{str(e)}\n\n"
                "支持 UTF-8/GBK 编码，日期时间、开高低收、成交量等常见中英文表头。")

    def download_online_data(self):
        """打开在线下载对话框，成功后按分钟/日线分别初始化数据"""
        dlg = DownloadDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_df is None:
            return

        df = dlg.result_df
        name = dlg.result_name
        code = dlg.code
        period_key = dlg.period_key

        try:
            if hasattr(self, '_user_target_datetime'):
                del self._user_target_datetime
            self._exit_fenshi_mode()
            self.imported_data = df
            self.imported_stock_name = name
            self.imported_stock_code = code
            self.source_period_key = period_key
            self.btn_clear_data.setEnabled(True)
            self.current_period = 'D'
            self._set_checked_period('D')

            if period_key in self.MINUTE_PERIOD_KEYS:
                # 分钟数据：作为原始分钟数据，合成日线后即可训练
                self.raw_min_data = df
                self.is_min_data = True
                self.period_data_cache.clear()
                self.period_data_cache[period_key] = df
                self._update_fenshi_button_state()
                self._synthesize_period_data_async('D', min_data_source=df)
            else:
                # 日线及以上：直接使用
                self.raw_min_data = None
                self.is_min_data = False
                self.period_data_cache['D'] = df
                self.stock_data_raw = df
                self._update_fenshi_button_state()
                self._precompute_indicators_async()

            self._update_data_label("在线下载", name, code, df)
            self.lbl_source_status.setText(f"● 在线 · {dlg.result_source}")
            self.lbl_source_status.setStyleSheet("color: #10B981; font-weight: 600;")
            self.statusBar().showMessage(
                f"在线行情已就绪 · {name}({code}) · {period_key} · {len(df):,} 根", 8000)
        except Exception as e:
            logger.error(f"在线下载数据处理失败: {e}", exc_info=True)
            QMessageBox.warning(self, "错误", f"下载数据加载失败：{str(e)}")

    def _update_data_label(self, file_path: str, stock_name: str, stock_code: str, df: pd.DataFrame):
        file_name = os.path.basename(file_path) if file_path else "内存数据"
        self.lbl_data_path.setText(
            f"已加载：{file_name}\n"
            f"股票：{stock_name}({stock_code})\n"
            f"共{len(df)}根K线\n"
            f"范围：{df.index[0].strftime('%Y-%m-%d %H:%M')} 至 {df.index[-1].strftime('%Y-%m-%d %H:%M')}"
        )
        self.lbl_cur_stock.setText(f"{stock_name}  {stock_code}")
        for key, button in self.period_buttons.items():
            if key.endswith('min'):
                button.setEnabled(self.source_period_key.endswith('min') and
                                  int(key[:-3]) >= int(self.source_period_key[:-3]))

        start_qt = QDateTime(df.index[0].to_pydatetime())
        end_qt = QDateTime(df.index[-1].to_pydatetime())
        self.de_start_date.setDateTimeRange(start_qt, end_qt)
        # 仅在当前选择超出新数据范围时才重置为数据中点，
        # 不覆盖用户已选择的起始时间
        if not (start_qt <= self.de_start_date.dateTime() <= end_qt):
            mid_idx = len(df) // 2
            mid_qt = QDateTime(df.index[mid_idx].to_pydatetime())
            self.de_start_date.setDateTime(mid_qt)

    def clear_imported_data(self):
        self.raw_min_data = None
        self.is_min_data = False
        self.source_period_key = 'D'
        self.period_data_cache.clear()
        self._exit_fenshi_mode(reset_button=True)
        self._user_exact_target = None
        self._user_target_pending = False
        self.imported_data = None
        self.imported_stock_name = ""
        self.imported_stock_code = ""
        self.lbl_data_path.setText("当前：未加载数据，请先导入")
        self.lbl_source_status.setText("● 尚未载入")
        self.lbl_source_status.setStyleSheet("color: #F59E0B; font-weight: 600;")
        self.lbl_cur_stock.setText("等待载入行情")
        self.lbl_cur_date.setText("未开始")
        self.lbl_chart_quote.setText("O --   H --   L --   C --   VOL --")
        self.btn_clear_data.setEnabled(False)

        self.lbl_tdx_folder.setText("未选择原始数据文件夹")
        self.lbl_tdx_folder.setStyleSheet("color: gray; font-size: 9px;")
        self.raw_stock_files.clear()
        self.cb_random_stock.setChecked(False)
        self.cb_specific_stock.setChecked(False)
        self.le_stock_code.clear()
        self.le_stock_code.setEnabled(False)

        self.period_buttons[self.current_period].setChecked(False)
        self.current_period = 'D'
        self.period_buttons['D'].setChecked(True)
        self._reset_collections()
        self.ax_kline.clear()
        self.ax_volume.clear()
        self.ax_macd.clear()
        self.ax_kline.set_visible(False)
        self.ax_volume.set_visible(False)
        self.ax_macd.set_visible(False)
        self.fig.canvas.draw_idle()
        self.statusBar().showMessage("数据已清除")

    # ----------------- 周期切换 -----------------
    def on_period_button_clicked(self, new_period_key):
        if new_period_key == self.current_period:
            return
        if any(thread is not None and thread.isRunning() for thread in (self.precompute_thread, self.synth_thread)):
            self._set_checked_period(self.current_period)
            return
        self.stop_playback()

        if new_period_key.endswith('min') and self.source_period_key.endswith('min'):
            source_minutes = int(self.source_period_key[:-3])
            target_minutes = int(new_period_key[:-3])
            if target_minutes < source_minutes:
                QMessageBox.information(
                    self, "周期不可用",
                    f"当前源数据是 {source_minutes} 分钟线，无法还原为 {target_minutes} 分钟线。\n"
                    f"请导入更细周期数据，或在线下载 {target_minutes} 分钟线。")
                self._set_checked_period(self.current_period)
                return

        if self._is_training_active_silent():
            self._pending_period_switch = {
                'current_dt': self.current_datetime, 'history_end_dt': self.history_end_datetime,
                'cursor_abs_idx': self.cursor_abs_idx if self.cursor_mode else -1}

        # 1分钟周期直接使用原始分钟数据，无需合成
        if new_period_key == '1min':
            if not self.is_min_data or self.raw_min_data is None:
                QMessageBox.warning(self, "错误", "请先导入1分钟K线数据才能使用1分钟周期！")
                self.period_buttons[self.current_period].setChecked(True)
                return
            # 与合成路径一致：训练中切换时保存位置，切换后按新周期重新映射
            if self._is_training_active_silent():
                self._pending_period_switch = {
                    'current_dt': self.current_datetime,
                    'history_end_dt': self.history_end_datetime,
                    'cursor_abs_idx': self.cursor_abs_idx if self.cursor_mode else -1
                }
            self.period_buttons[self.current_period].setChecked(False)
            self.period_buttons[new_period_key].setChecked(True)
            self.period_data_cache['1min'] = self.raw_min_data
            self._switch_period(new_period_key)
            return

        # 如果目标周期已在缓存中，直接切换
        if new_period_key in self.period_data_cache:
            self.period_buttons[self.current_period].setChecked(False)
            self.period_buttons[new_period_key].setChecked(True)
            self._switch_period(new_period_key)
            return

        # 日线也可以合成周/月线，无需假装必须有1分钟数据。
        if not self.is_min_data and not new_period_key.endswith('min'):
            self._synthesize_period_data_async(new_period_key, self.imported_data)
            return
        if not self.is_min_data:
            QMessageBox.warning(self, "错误", "请先导入1分钟K线数据才能使用分钟级周期！")
            self.period_buttons[self.current_period].setChecked(True)
            return

        # 确保 raw_min_data 可用
        if self.raw_min_data is None:
            if self.imported_data is not None:
                self.raw_min_data = self.imported_data
            else:
                QMessageBox.warning(self, "错误", "分钟数据已丢失，请重新导入！")
                self.is_min_data = False
                self.period_buttons[self.current_period].setChecked(True)
                return

        # 保存训练状态（如果训练已激活）
        if self._is_training_active_silent():
            self._pending_period_switch = {
                'current_dt': self.current_datetime,
                'history_end_dt': self.history_end_datetime,
                'cursor_abs_idx': self.cursor_abs_idx if self.cursor_mode else -1
            }

        self.period_buttons[self.current_period].setChecked(False)
        self.period_buttons[new_period_key].setChecked(True)

        # 调用合成方法（传入分钟数据源）
        self._synthesize_period_data_async(new_period_key, self.raw_min_data)

    def _switch_period(self, period_key):
        if period_key in self.period_data_cache:
            self.stock_data_raw = self.period_data_cache[period_key]
            self.current_period = period_key
            self._update_header_period(period_key)
            self._update_fenshi_button_state()
            self._precompute_indicators_async()
        else:
            # 这种情况应该不会发生，因为我们在 on_period_button_clicked 中已经处理了缓存缺失
            QMessageBox.warning(self, "错误", f"无法切换周期，数据未准备就绪")
            self.period_buttons[self.current_period].setChecked(True)

    def _synthesize_period_data_async(self, period_key, min_data_source):
        if min_data_source is None:
            QMessageBox.warning(self, "错误", f"无法合成{period_key}周期，分钟数据丢失！")
            return

        self.progress_dialog = QProgressDialog(f"正在合成{period_key}周期数据...", "取消", 0, 0, self)
        self.progress_dialog.setCancelButton(None)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.synth_thread = SynthesizePeriodThread(min_data_source, period_key)
        self.synth_thread.finished.connect(self.on_synthesize_finished)
        self.synth_thread.error.connect(self.on_synthesize_error)
        # 修复(2026-08-31)：不使用 deleteLater，见扫描线程处说明
        self.progress_dialog.canceled.connect(self.synth_thread.requestInterruption)
        self.synth_thread.start()

    def on_synthesize_finished(self, df_period, period_key):
        self.progress_dialog.close()
        self.period_data_cache[period_key] = df_period
        self.stock_data_raw = df_period
        self.current_period = period_key
        self._update_header_period(period_key)
        self._update_fenshi_button_state()
        self._precompute_indicators_async()

    def on_synthesize_error(self, error_msg):
        self.progress_dialog.close()
        QMessageBox.critical(self, "合成错误", error_msg)
        self.period_buttons[self.current_period].setChecked(False)
        self.period_buttons['D'].setChecked(True)
        self.current_period = 'D'

    def _precompute_indicators_async(self):
        if self.stock_data_raw is None:
            return
        self.progress_dialog = QProgressDialog("正在计算技术指标...", "取消", 0, 100, self)
        self.progress_dialog.setCancelButton(None)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.precompute_thread = PrecomputeIndicatorsThread(self.stock_data_raw)
        self.precompute_thread.progress.connect(self.on_precompute_progress)
        self.precompute_thread.finished.connect(self.on_precompute_finished)
        self.precompute_thread.error.connect(self.on_precompute_error)
        # 修复(2026-08-31)：不使用 deleteLater，见扫描线程处说明
        self.progress_dialog.canceled.connect(self.precompute_thread.requestInterruption)
        self.precompute_thread.start()

    def on_precompute_progress(self, value):
        self.progress_dialog.setValue(value)

    def on_precompute_finished(self, df_with_indicators):
        self.progress_dialog.close()
        self.stock_data = df_with_indicators
        self.trading_days = self.stock_data.index.tolist()

        if getattr(self, '_restore_payload', None) is not None:
            payload = self._restore_payload
            self._restore_payload = None
            self._complete_restore(payload)
        elif self._pending_period_switch is not None:
            old_info = self._pending_period_switch
            self._pending_period_switch = None
            self._finalize_period_switch(old_info)
        else:
            if hasattr(self, '_user_target_datetime'):
                self._continue_start_training(self._user_target_datetime)
            else:
                self._draw_combined_chart()
        self.precompute_thread = None

    def on_precompute_error(self, error_msg):
        self._restore_payload = None
        self.progress_dialog.close()
        QMessageBox.critical(self, "计算错误", f"计算技术指标时出错：{error_msg}")
        self.btn_start.setEnabled(True)   # 错误时重新启用开始按钮
        self.precompute_thread = None

    def _dt_to_period_label(self, dt: datetime, period_key: str) -> pd.Timestamp:
        ts = pd.Timestamp(dt)
        if period_key.endswith('min'):
            anchor = ts.normalize() + pd.Timedelta(hours=9, minutes=30)
            return anchor + (ts - anchor).ceil(pd.Timedelta(minutes=int(period_key[:-3])))
        elif period_key == 'D':
            return pd.Timestamp(dt.date())
        elif period_key == 'W':
            monday = ts.normalize() - pd.Timedelta(days=ts.weekday())
            return monday + pd.Timedelta(days=4)
        elif period_key == 'M':
            return ts.normalize() + pd.offsets.MonthEnd(0)
        else:
            return ts

    def _finalize_period_switch(self, old_info):
        current_dt = old_info['current_dt']
        history_end_dt = old_info['history_end_dt']
        cursor_idx = old_info['cursor_abs_idx']

        # 分钟级数据的首次日内周期切换：按用户选择的精确起始时刻定位，
        # 而不是按日线起点（否则时间部分被丢弃，永远从当天第一根K线开始）。
        # 仅当训练仍停在起始边界（尚未推进K线）时生效，用后即清除。
        use_exact = (self._user_target_pending and self._user_exact_target is not None
                     and self.current_period in self.MINUTE_PERIOD_KEYS
                     and current_dt == history_end_dt)
        if use_exact:
            # 精确时刻 T：历史展示到最后一根结束时间 <= T 的K线，
            # 下一根（T 之后）为第一根可交易K线
            label_current = pd.Timestamp(self._user_exact_target)
            self._user_target_pending = False
        else:
            label_current = self._dt_to_period_label(current_dt, self.current_period)
        label_history = self._dt_to_period_label(history_end_dt, self.current_period)

        idx_array = np.array(self.trading_days)
        pos = np.searchsorted(idx_array, label_current, side='right') - 1
        self.current_date_idx = int(max(0, pos))
        self.current_datetime = current_dt
        self.current_date = current_dt
        self._apply_revealed_aggregate(current_dt)

        pos = np.searchsorted(idx_array, label_history, side='right') - 1
        self.history_end_idx = int(max(0, pos))
        if current_dt > history_end_dt and self.history_end_idx >= self.current_date_idx:
            self.history_end_idx = self.current_date_idx - 1
        if use_exact:
            # 保持与日线起始一致的边界语义：位于历史边界时不可直接交易，
            # 需先点「下一根K线」
            self.history_end_idx = self.current_date_idx

        self.next_idx = self.current_date_idx + 1

        self.display_start_idx = max(0, self.current_date_idx - 60)
        self.display_end_idx = self.current_date_idx

        self.cursor_mode = False
        self.cursor_abs_idx = -1

        self._draw_combined_chart()
        self._update_status_ui()
        self._update_trade_buttons_state()

    def _apply_revealed_aggregate(self, cutoff):
        """切换粗周期时，当前蜡烛仅使用截止已揭示时刻的原始行情。"""
        raw = self.raw_min_data if self.is_min_data else self.imported_data
        if raw is None or self.current_date_idx < 0 or self.current_period == self.source_period_key:
            return
        boundary = self.stock_data.index[self.current_date_idx]
        if self.current_period.endswith('min'):
            start = boundary - pd.Timedelta(minutes=int(self.current_period[:-3]))
            subset = raw[(raw.index > start) & (raw.index <= pd.Timestamp(cutoff))]
        else:
            start = (boundary.normalize() if self.current_period == 'D' else
                     boundary.normalize() - pd.Timedelta(days=boundary.weekday()) if self.current_period == 'W' else
                     boundary.normalize().replace(day=1))
            subset = raw[(raw.index >= start) & (raw.index <= pd.Timestamp(cutoff))]
        if subset.empty:
            return
        row = self.stock_data.index[self.current_date_idx]
        self.stock_data.loc[row, ['Open', 'High', 'Low', 'Close', 'Volume']] = [
            subset.Open.iloc[0], subset.High.max(), subset.Low.min(), subset.Close.iloc[-1], subset.Volume.sum()]
        close = self.stock_data.Close
        for length in (5, 10, 20, 60):
            self.stock_data[f'MA{length}'] = close.rolling(length, min_periods=1).mean()
        self.stock_data['DIF'] = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
        self.stock_data['DEA'] = self.stock_data.DIF.ewm(span=9, adjust=False).mean()
        self.stock_data['MACD'] = 2 * (self.stock_data.DIF - self.stock_data.DEA)

    # ----------------- 分时图/K线图视图切换 -----------------
    def toggle_fenshi_view(self):
        """切换分时图/K线图视图（训练中可随时切换，不动训练状态）"""
        if not self.btn_fenshi.isEnabled():
            return
        if self.view_mode == 'fenshi':
            self._exit_fenshi_mode()
        else:
            self.view_mode = 'fenshi'
            self._kline_win_backup = (self.display_start_idx, self.display_end_idx)
            self._clamp_display_to_day()
            self.btn_fenshi.setChecked(True)
        self._draw_combined_chart()

    def _exit_fenshi_mode(self, reset_button: bool = False):
        """退出分时模式，恢复进入前的K线显示窗口"""
        if self.view_mode == 'fenshi':
            self.view_mode = 'kline'
            if self._kline_win_backup is not None:
                self.display_start_idx, self.display_end_idx = self._kline_win_backup
                self._kline_win_backup = None
        self.btn_fenshi.setChecked(False)
        if reset_button:
            self.btn_fenshi.setEnabled(False)

    def _update_fenshi_button_state(self):
        """分时图按钮仅对分钟级周期可用"""
        if self.is_min_data and self.current_period in self.MINUTE_PERIOD_KEYS:
            self.btn_fenshi.setEnabled(True)
        else:
            self._exit_fenshi_mode(reset_button=True)

    def _day_start_idx(self) -> int:
        """当前训练日的第一根K线下标"""
        if not self.trading_days or self.current_datetime is None:
            return 0
        day = self.current_datetime.date()
        for i, ts in enumerate(self.trading_days):
            if ts.date() == day:
                return i
        return max(0, self.current_date_idx)

    def _clamp_display_to_day(self):
        """分时模式下把显示窗口钳制在当前训练日内（跨日时自动跳到新日）"""
        if self.view_mode != 'fenshi' or self.current_date_idx < 0:
            return
        day_start = self._day_start_idx()
        if self.display_start_idx < day_start:
            self.display_start_idx = day_start
        if self.display_end_idx > self.current_date_idx:
            self.display_end_idx = self.current_date_idx

    def _set_checked_period(self, period_key: str):
        """取消所有周期按钮勾选，仅勾选指定周期"""
        for key, btn in self.period_buttons.items():
            btn.setChecked(key == period_key)
        self._update_header_period(period_key)

    def _update_header_period(self, period_key: str):
        labels = {
            '1min': '1分钟', '5min': '5分钟', '15min': '15分钟',
            '20min': '20分钟', '30min': '30分钟', '60min': '60分钟',
            'D': '日线', 'W': '周线', 'M': '月线',
        }
        self.lbl_header_period.setText(labels.get(period_key, period_key))

    def _set_session_mode(self, active: bool):
        """在开局页与交易工作区之间切换，避免未开始时出现空图表。"""
        self.workspace_stack.setCurrentWidget(self.trading_page if active else self.setup_page)
        self.btn_save_session.setEnabled(active)

    # ----------------- 训练控制 -----------------
    def start_training(self):
        self.stop_playback()
        # 优先使用已导入的单个文件数据
        if self.imported_data is not None:
            df = self.imported_data
            stock_name = self.imported_stock_name
            stock_code = self.imported_stock_code
        elif self.raw_stock_files:
            if self.cb_random_stock.isChecked():
                display_name = random.choice(list(self.raw_stock_files.keys()))
                file_path, stock_code, file_type = self.raw_stock_files[display_name]
            elif self.cb_specific_stock.isChecked():
                code = self.le_stock_code.text().strip()
                if not validate_stock_code(code):
                    QMessageBox.warning(self, "错误", "请输入正确的6位数字股票代码！")
                    return
                matched = None
                for disp_name, (path, scode, ftype) in self.raw_stock_files.items():
                    if scode == code:
                        matched = disp_name
                        file_path = path
                        stock_code = scode
                        file_type = ftype
                        break
                if not matched:
                    QMessageBox.warning(self, "错误", f"未找到股票代码 {code} 的 .txt 或 .day 文件！")
                    return
            else:
                QMessageBox.warning(self, "错误", "请选择股票选择方式（随机或指定代码）！")
                return

            try:
                df, code, name = load_stock_data_file(file_path, file_type)
                if df is None or df.empty:
                    raise ValueError("解析后的数据为空")
                if not name and code in self.stock_names:
                    name = self.stock_names[code]
                stock_name = name
                stock_code = code
            except Exception as e:
                logger.error(f"加载文件失败 {file_path}: {e}", exc_info=True)
                QMessageBox.critical(self, "加载失败", f"无法解析文件：{file_path}\n错误：{str(e)}")
                return
        else:
            QMessageBox.warning(self, "错误", "请先导入数据（单个文件或扫描文件夹）！")
            return

        try:
            initial_capital = float(self.le_initial_capital.text())
            fee_rate = float(self.le_fee_rate.text())
            if initial_capital <= 0:
                raise ValueError("初始资金必须大于0")
            if fee_rate < 0 or fee_rate > 1:
                raise ValueError("费率必须在0-1之间")

            qdt = self.de_start_date.dateTime()
            user_target_datetime = qdt.toPyDateTime()

            # 根据数据来源决定处理方式
            if self.is_min_data:
                # 分钟数据（可能是之前导入的，也可能是通过文件夹加载的分钟数据？但文件夹扫描不加载具体数据，所以这里一般指之前导入的）
                self.raw_min_data = df if self.raw_min_data is None else self.raw_min_data
                self.imported_data = df
                self.imported_stock_name = stock_name
                self.imported_stock_code = stock_code
                self._user_target_datetime = user_target_datetime
                # 记录用户选择的精确起始时刻：首次切换到分钟周期时按此定位，
                # 否则选择的时间会被丢弃（日线定位只用日期，永远从当天第一根K线开始）
                self._user_exact_target = user_target_datetime
                self._user_target_pending = True
                self.period_data_cache.clear()
                # 按原始分钟周期训练，不能丢弃用户选定的分钟时间。
                self.stock_data_raw = self.raw_min_data
                self.current_period = self.source_period_key
                self.period_data_cache[self.current_period] = self.raw_min_data
                self._set_checked_period(self.current_period)
                self._update_fenshi_button_state()
                self._user_target_pending = False
                self._precompute_indicators_async()
            else:
                # 日线数据
                self.stock_data_raw = df
                self.imported_data = df
                self.imported_stock_name = stock_name
                self.imported_stock_code = stock_code
                self.period_data_cache['D'] = df
                self.current_period = 'D'
                self.period_buttons['D'].setChecked(True)
                self._update_fenshi_button_state()
                self._user_target_datetime = user_target_datetime
                self._user_exact_target = None
                self._user_target_pending = False
                self._precompute_indicators_async()

            self._set_trade_buttons_enabled(False)
            self.btn_start.setEnabled(False)

        except ValueError as e:
            QMessageBox.warning(self, "参数错误", str(e))
        except Exception as e:
            logger.error(f"启动训练时发生错误: {e}", exc_info=True)
            QMessageBox.critical(self, "系统错误", f"启动训练时发生错误：{str(e)}")

    def _continue_start_training(self, user_target_datetime):
        try:
            if self.stock_data is None or self.stock_data.empty:
                raise ValueError("股票数据为空，无法开始训练")
            if self.stock_data.index.empty:
                raise ValueError("股票数据索引为空")

            required_cols = ['MA5', 'MA10', 'MA20', 'MA60', 'DIF', 'DEA', 'MACD']
            missing = [col for col in required_cols if col not in self.stock_data.columns]
            if missing:
                raise ValueError(f"指标列缺失: {missing}")

            first_day = self.trading_days[0]
            if first_day.hour == 0 and first_day.minute == 0:
                user_target = user_target_datetime.date()
            else:
                user_target = user_target_datetime

            if isinstance(user_target, date) and not isinstance(user_target, datetime):
                compare_target = datetime.combine(user_target, datetime.min.time())
            else:
                compare_target = user_target

            target_idx = None
            for i, day in enumerate(self.trading_days):
                if pd.Timestamp(day) >= pd.Timestamp(compare_target):
                    target_idx = i
                    break

            if target_idx is None:
                raise ValueError(f"设定的时间 {user_target_datetime.strftime('%Y-%m-%d %H:%M')} 不在数据范围内")
            start_note = ""
            if target_idx == 0:
                if len(self.trading_days) < 3:
                    raise ValueError("至少需要3根K线才能展示历史并开始训练")
                # 首根没有上下文，自动留出历史窗口，而非让默认起点无法启动。
                target_idx = min(60, len(self.trading_days) - 1)
                start_note = f"已自动保留 {target_idx} 根历史K线 · "

            self.history_end_idx = target_idx - 1
            self.current_date_idx = self.history_end_idx
            self.next_idx = target_idx
            self.current_date = self.trading_days[self.current_date_idx]
            self.current_datetime = self.current_date
            self.history_end_datetime = self.trading_days[self.history_end_idx]

            self.simulator = TradingSimulator(float(self.le_initial_capital.text()),
                                              float(self.le_fee_rate.text()),
                                              allow_t0=self.cb_t0.isChecked())
            self.simulator.set_current_stock(self.imported_stock_code)
            self.session_finished = False
            self.cursor_mode = True
            self.cursor_abs_idx = self.current_date_idx
            self._cursor_price = None
            self.drawings = []
            self._trend_anchor = None

            self.display_start_idx = max(0, self.current_date_idx - 60)
            self.display_end_idx = self.current_date_idx

            self._reset_collections()
            self._update_trade_buttons_state()
            self._update_status_ui()
            self._draw_combined_chart()
            self._clear_trade_history()

            self._save_settings()
            self._set_session_mode(True)
            self.statusBar().showMessage(
                f"{start_note}复盘已开始 · 首根待交易K线 {self.trading_days[self.next_idx].strftime('%Y-%m-%d %H:%M')}",
                10000)

        except ValueError as e:
            self.btn_start.setEnabled(True)
            QMessageBox.warning(self, "参数错误", str(e))
        except Exception as e:
            logger.error(f"继续训练时发生错误: {e}", exc_info=True)
            QMessageBox.critical(self, "系统错误", f"继续训练时发生错误：{str(e)}")

    def reset_training(self, keep_data: bool = False):
        self.stop_playback()
        prompt = ("结束当前训练并保留已加载行情吗？"
                  if keep_data else "确定要重置所有训练数据吗？")
        reply = QMessageBox.question(self, "确认重置",
                                     prompt,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.session_finished = False
            self.drawings = []
            self._trend_anchor = None
            if hasattr(self, '_user_target_datetime'):
                del self._user_target_datetime
            # 修复(2026-08-31)：原 plt.close('all') 会销毁画布绑定的 Figure，
            # 重置后再次训练时在死图重绘会原生崩溃(0xC0000409)。
            # 改为安全地清空三个子图（与 clear_imported_data 一致）
            self._reset_collections()
            self.ax_kline.clear()
            self.ax_volume.clear()
            self.ax_macd.clear()
            self.ax_kline.set_visible(False)
            self.ax_volume.set_visible(False)
            self.ax_macd.set_visible(False)
            self.canvas.draw_idle()
            self.simulator.reset()
            if not keep_data:
                self.stock_data = None
                self.stock_data_raw = None
                self.trading_days = []
                self.raw_min_data = None
                self.is_min_data = False
                self.period_data_cache.clear()
            self.current_date_idx = -1
            self.current_date = None
            self.history_end_idx = -1
            self.next_idx = -1
            self.display_start_idx = 0
            self.display_end_idx = -1
            self.current_datetime = None
            self.history_end_datetime = None
            if not keep_data:
                self.imported_data = None
                self.imported_stock_name = ""
                self.imported_stock_code = ""
                self.period_buttons[self.current_period].setChecked(False)
                self.current_period = 'D'
                self.period_buttons['D'].setChecked(True)
                self._update_fenshi_button_state()
            else:
                self._exit_fenshi_mode()
            self._user_exact_target = None
            self._user_target_pending = False
            self._set_trade_buttons_enabled(False)
            self._update_status_ui(reset=True)
            self._clear_trade_history()

            # 清理线程
            if self.precompute_thread is not None:
                try:
                    self.precompute_thread.progress.disconnect()
                    self.precompute_thread.finished.disconnect()
                    self.precompute_thread.error.disconnect()
                except:
                    pass
                if self.precompute_thread.isRunning():
                    self.precompute_thread.requestInterruption()
                    self.precompute_thread.wait(2000)
                self.precompute_thread = None

            if self.scan_thread is not None:
                try:
                    self.scan_thread.progress.disconnect()
                    self.scan_thread.finished.disconnect()
                    self.scan_thread.error.disconnect()
                except:
                    pass
                if self.scan_thread.isRunning():
                    self.scan_thread.requestInterruption()
                    self.scan_thread.wait(2000)
                self.scan_thread = None

            if self.synth_thread is not None:
                try:
                    self.synth_thread.finished.disconnect()
                    self.synth_thread.error.disconnect()
                except:
                    pass
                if self.synth_thread.isRunning():
                    self.synth_thread.requestInterruption()
                    self.synth_thread.wait(2000)
                self.synth_thread = None

            self.cursor_mode = False
            self.cursor_abs_idx = -1
            self._reset_collections()

            if keep_data and self.stock_data is not None:
                self._draw_combined_chart()
            else:
                self.ax_kline.clear()
                self.ax_volume.clear()
                self.ax_macd.clear()
                self.ax_kline.set_visible(False)
                self.ax_volume.set_visible(False)
                self.ax_macd.set_visible(False)
                self.fig.canvas.draw_idle()

            # 重新启用开始按钮
            self.btn_start.setEnabled(True)
            self._set_session_mode(False)
            if not keep_data:
                self.lbl_data_path.setText("当前：未加载数据，请先导入")
                self.lbl_source_status.setText("● 尚未载入")
                self.lbl_source_status.setStyleSheet("color: #F59E0B; font-weight: 600;")
                self.lbl_cur_stock.setText("等待载入行情")
                self.lbl_cur_date.setText("未开始")
                self.lbl_chart_quote.setText("O --   H --   L --   C --   VOL --")
            elif self.imported_data is not None:
                self.lbl_cur_stock.setText(
                    f"{self.imported_stock_name}  {self.imported_stock_code}")
            self.statusBar().showMessage(
                "本轮训练已结束，行情数据已保留" if keep_data else "训练状态已重置", 6000)

    # ----------------- 交易操作 -----------------
    def _on_buy_clicked(self):
        if not self._check_training_active():
            return
        if self.current_date_idx <= self.history_end_idx:
            QMessageBox.warning(self, "无法交易", "当前处于历史展示阶段，不能进行交易。请先点击“下一根K线”进入待交易K线。")
            return

        try:
            price = float(self.le_trade_price.text())
        except ValueError:
            QMessageBox.warning(self, "输入错误", "请输入有效的价格")
            return

        if price <= 0:
            QMessageBox.warning(self, "输入错误", "价格必须大于0")
            return

        ratio_index = self.ratio_combo.currentIndex()
        ratios = [None, 1.0, 0.5, 1.0/3.0, 0.25]
        ratio = ratios[ratio_index]
        if ratio is not None:
            fee_rate = float(self.simulator.fee_rate)
            cost_per_share = price * (1 + fee_rate)
            available = self.simulator.current_capital_float
            max_shares = int(available / cost_per_share)
            target = int(max_shares * ratio)
            target = (target // 100) * 100
            if target < 100 and target > 0:
                target = 100
            if target < 100:
                QMessageBox.warning(self, '资金不足', '当前资金不足以按所选仓位买入一手')
                return
            self.sb_trade_amount.setValue(target)
        self.buy_stock()

    def _on_sell_clicked(self):
        if not self._check_training_active():
            return
        if self.current_date_idx <= self.history_end_idx:
            QMessageBox.warning(self, "无法交易", "当前处于历史展示阶段，不能进行交易。请先点击“下一根K线”进入待交易K线。")
            return

        ratio_index = self.ratio_combo.currentIndex()
        ratios = [None, 1.0, 0.5, 1.0/3.0, 0.25]
        ratio = ratios[ratio_index]
        if ratio is not None:
            hold = self.simulator.get_sellable_hold(self.current_date)
            target = int(hold * ratio)
            target = (target // 100) * 100
            if target <= 0:
                QMessageBox.warning(self, '无法卖出', '当前没有可按所选仓位卖出的持仓（请检查T+1限制）')
                return
            self.sb_trade_amount.setValue(target)
        self.sell_stock()

    def buy_stock(self):
        if not self._check_training_active():
            return
        if self.current_date_idx <= self.history_end_idx:
            QMessageBox.warning(self, "无法交易", "当前处于历史展示阶段，不能进行交易。请先点击“下一根K线”进入待交易K线。")
            return
        try:
            price = float(self.le_trade_price.text())
            amount = self.sb_trade_amount.value()
            self._validate_trade_price(price)
            can_buy, error_msg = self.simulator.can_buy(price, amount)
            if not can_buy:
                QMessageBox.warning(self, "无法买入", error_msg)
                return
            record = self.simulator.buy(price, amount, self.imported_stock_name, self.current_date)
            if record:
                self._update_status_ui()
                self._update_trade_history()
                self.statusBar().showMessage(
                    f"买入成交 · {amount}股 @ {price:.2f} · 手续费 {record.fee:.2f}", 7000)
                self._draw_combined_chart()
        except ValueError as error:
            QMessageBox.warning(self, "输入错误", str(error))

    def sell_stock(self):
        if not self._check_training_active():
            return
        if self.current_date_idx <= self.history_end_idx:
            QMessageBox.warning(self, "无法交易", "当前处于历史展示阶段，不能进行交易。请先点击“下一根K线”进入待交易K线。")
            return
        try:
            price = float(self.le_trade_price.text())
            amount = self.sb_trade_amount.value()
            self._validate_trade_price(price)
            can_sell, error_msg = self.simulator.can_sell(price, amount, self.current_date)
            if not can_sell:
                QMessageBox.warning(self, "无法卖出", error_msg)
                return
            record = self.simulator.sell(price, amount, self.imported_stock_name, self.current_date)
            if record:
                self._update_status_ui()
                self._update_trade_history()
                self.statusBar().showMessage(
                    f"卖出成交 · {amount}股 @ {price:.2f} · 本笔盈亏 {record.realized_pnl:+.2f}", 7000)
                self._draw_combined_chart()
        except ValueError as error:
            QMessageBox.warning(self, "输入错误", str(error))

    def _validate_trade_price(self, price):
        if self.session_finished:
            raise ValueError('本轮训练已结束，请开始新一轮')
        row = self.stock_data.iloc[self.current_date_idx]
        if not np.isfinite(price) or not float(row['Low']) - .005 <= price <= float(row['High']) + .005:
            raise ValueError(f"模拟成交价须在当前K线区间 {row['Low']:.2f}～{row['High']:.2f} 内")

    def next_trading_day(self):
        try:
            if not self._check_training_active():
                return
            if self.session_finished:
                return
            if self.next_idx >= len(self.trading_days):
                self.finish_session()
                return
            previous_idx = self.current_date_idx
            self.current_date_idx = self.next_idx
            self.next_idx += 1
            # 上一根粗周期蜡烛现在已完成，恢复完整行情后再计算当前截止点。
            columns = ['Open', 'High', 'Low', 'Close', 'Volume']
            self.stock_data.loc[self.stock_data.index[previous_idx], columns] = self.stock_data_raw.iloc[previous_idx][columns]
            self.current_date = self.trading_days[self.current_date_idx]
            if self.current_period in ('D', 'W', 'M'):
                self.current_date = pd.Timestamp(self.current_date) + pd.Timedelta(hours=15)
            self.current_datetime = self.current_date
            self._apply_revealed_aggregate(self.current_datetime)
            self._adjust_display_to_include_current()
            self._update_trade_buttons_state()
            self._update_status_ui()
            self._draw_combined_chart()
        except Exception as e:
            import traceback
            logger.error(f"下一K线错误: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "程序错误", f"发生未预期错误：{str(e)}\n请查看日志文件 trainer.log")

    # ----------------- 辅助方法 -----------------
    def _update_trade_buttons_state(self):
        if not self._check_training_active():
            self._set_trade_buttons_enabled(False)
            return
        trade_enabled = bool(self.current_date_idx > self.history_end_idx and not self.session_finished)
        self.btn_buy.setEnabled(trade_enabled)
        self.btn_sell.setEnabled(trade_enabled)
        # 修复(2026-08-31)：不切换 le_trade_price 的 enabled/readOnly 状态
        # （PyQt6 6.9.x 在周期切换回调链中触发原生崩溃），改用样式提示
        self.le_trade_price.setStyleSheet(
            "color: #888888; background-color: #333333;" if not trade_enabled else
            "color: #E6E9ED; background-color: #0D1116;")
        self.sb_trade_amount.setEnabled(trade_enabled)
        self.btn_qty_minus.setEnabled(trade_enabled)
        self.btn_qty_plus.setEnabled(trade_enabled)
        self.ratio_combo.setEnabled(trade_enabled)
        self.btn_next.setEnabled(not self.session_finished)
        self.btn_play.setEnabled(not self.session_finished)

    def _adjust_display_to_include_current(self):
        if self.current_date_idx < 0:
            return
        if self.display_end_idx < self.current_date_idx:
            window_size = self.display_end_idx - self.display_start_idx + 1
            self.display_end_idx = self.current_date_idx
            self.display_start_idx = max(0, self.display_end_idx - window_size + 1)

    def _pan_left(self, steps: int = None):
        if steps is None:
            steps = self.ZOOM_STEP
        self._pan(-steps)

    def _pan_right(self, steps: int = None):
        if steps is None:
            steps = self.ZOOM_STEP
        self._pan(steps)

    def _pan(self, delta: int):
        if self.stock_data is None or self.current_date_idx < 0:
            return
        window_size = self.display_end_idx - self.display_start_idx + 1
        new_start = self.display_start_idx + delta
        new_end = self.display_end_idx + delta
        if new_start < 0:
            new_start = 0
            new_end = min(self.current_date_idx, new_start + window_size - 1)
        if new_end > self.current_date_idx:
            new_end = self.current_date_idx
            new_start = max(0, new_end - window_size + 1)
        if new_start <= self.current_date_idx and new_end >= new_start:
            self.display_start_idx = new_start
            self.display_end_idx = new_end
            self._clamp_display_to_day()
            self._draw_combined_chart()

    def _zoom_in(self):
        self._zoom(0.8)

    def _zoom_out(self):
        self._zoom(1.2)

    def _zoom(self, factor: float):
        if self.stock_data is None or self.current_date_idx < 0:
            return
        window_size = self.display_end_idx - self.display_start_idx + 1
        max_window = self.current_date_idx + 1
        new_window_size = int(window_size * factor)
        new_window_size = max(self.MIN_DISPLAY_COUNT, min(max_window, new_window_size))
        if new_window_size == window_size:
            return
        center = (self.display_start_idx + self.display_end_idx) // 2
        half = new_window_size // 2
        new_start = center - half
        new_end = new_start + new_window_size - 1
        if new_start < 0:
            new_start = 0
            new_end = new_start + new_window_size - 1
        if new_end > self.current_date_idx:
            new_end = self.current_date_idx
            new_start = max(0, new_end - new_window_size + 1)
        self.display_start_idx = new_start
        self.display_end_idx = new_end
        self._clamp_display_to_day()
        self._draw_combined_chart()

    def _check_training_active(self) -> bool:
        if not self.simulator.current_stock or self.current_date is None:
            QMessageBox.warning(self, "操作错误", "请先开始训练！")
            return False
        return True

    def _is_training_active_silent(self) -> bool:
        return self.simulator.current_stock is not None and self.current_date is not None

    def _get_current_price(self) -> float:
        """获取当前K线的收盘价，若为NaN则返回0.0"""
        if self.stock_data is not None and self.current_date_idx >= 0:
            try:
                # 使用 .iat 确保标量访问，避免 loc 可能返回 Series 的问题
                price = self.stock_data['Close'].iat[self.current_date_idx]
            except IndexError:
                return 0.0
            if pd.isna(price):
                return 0.0
            return float(price)
        return 0.0

    # ========== 光标模式方法 ==========
    def _move_cursor_left(self):
        if not self.cursor_mode or self.stock_data is None:
            self.cursor_mode = True
            self.cursor_abs_idx = self.current_date_idx
        else:
            self.cursor_abs_idx = max(0, self.cursor_abs_idx - 1)
        window_changed = self._ensure_cursor_visible()
        if window_changed:
            self._draw_combined_chart()
        else:
            self._update_cursor_overlay()

    def _move_cursor_right(self):
        if not self.cursor_mode or self.stock_data is None:
            self.cursor_mode = True
            self.cursor_abs_idx = self.current_date_idx
        else:
            # 训练模式下未来 K 线尚未揭示，光标最右只能到当前可见 K 线。
            max_idx = self.current_date_idx
            self.cursor_abs_idx = min(max_idx, self.cursor_abs_idx + 1)
        window_changed = self._ensure_cursor_visible()
        if window_changed:
            self._draw_combined_chart()
        else:
            self._update_cursor_overlay()

    def _move_cursor_to_index(self, idx: int, fast: bool = False, price=None):
        if self.stock_data is None:
            return
        if self.session_finished:
            self._set_trade_buttons_enabled(False)
            self.btn_play.setEnabled(False)
            return
        self.btn_play.setEnabled(True)
        max_idx = min(len(self.stock_data) - 1, self.current_date_idx)
        if idx < 0 or idx > max_idx:
            return
        if self.cursor_mode and self.cursor_abs_idx == idx and self._cursor_price == price:
            return
        self._cursor_price = price
        self.cursor_mode = True
        self.cursor_abs_idx = idx
        window_changed = self._ensure_cursor_visible()
        if fast and not window_changed:
            self._update_cursor_overlay()
        else:
            self._draw_combined_chart()

    def _move_cursor_to_data_x(self, x_data: float, fast: bool = False, price=None):
        if self.stock_data is None:
            return
        start_idx = self.display_start_idx
        # floor(x+0.5) 将整根蜡烛的柱宽稳定映射到对应索引，且不会受
        # Python round 的“银行家舍入”影响。
        local_idx = int(np.floor(x_data + 0.5))
        global_idx = start_idx + local_idx
        global_idx = max(0, min(self.current_date_idx, global_idx))
        self._move_cursor_to_index(global_idx, fast=fast, price=price)

    def _ensure_cursor_visible(self):
        old_window = (self.display_start_idx, self.display_end_idx)
        if self.cursor_abs_idx < self.display_start_idx:
            window_size = self.display_end_idx - self.display_start_idx + 1
            self.display_start_idx = max(0, self.cursor_abs_idx)
            self.display_end_idx = min(self.current_date_idx, self.display_start_idx + window_size - 1)
        elif self.cursor_abs_idx > self.display_end_idx:
            window_size = self.display_end_idx - self.display_start_idx + 1
            self.display_end_idx = min(self.current_date_idx, self.cursor_abs_idx)
            self.display_start_idx = max(0, self.display_end_idx - window_size + 1)
        self._clamp_display_to_day()
        return old_window != (self.display_start_idx, self.display_end_idx)

    def _create_cursor_artists(self, start_idx: int, end_idx: int):
        """创建动画光标线；这些线不会参与整图 draw，只由 blit 绘制。"""
        self.cursor_artists = {
            'kline_v': self.ax_kline.axvline(0, color='#E2E8F0', linewidth=0.9,
                                             alpha=0.6, linestyle='--', animated=True, zorder=20),
            'volume_v': self.ax_volume.axvline(0, color='#E2E8F0', linewidth=0.9,
                                               alpha=0.6, linestyle='--', animated=True, zorder=20),
            'macd_v': self.ax_macd.axvline(0, color='#E2E8F0', linewidth=0.9,
                                           alpha=0.6, linestyle='--', animated=True, zorder=20),
            'kline_h': self.ax_kline.axhline(0, color='#E2E8F0', linewidth=0.9,
                                             alpha=0.6, linestyle='--', animated=True, zorder=20),
        }
        time_axis = self.ax_macd if self.ax_macd.get_visible() else self.ax_volume if self.ax_volume.get_visible() else self.ax_kline
        self.cursor_time_axis = time_axis
        label_style = {'facecolor': '#39424E', 'edgecolor': 'none', 'pad': 2}
        self.cursor_artists['price_label'] = self.ax_kline.text(1, 0, '',
            transform=self.ax_kline.get_yaxis_transform(), va='center', fontsize=8,
            color='white', bbox=label_style, animated=True, zorder=25, clip_on=False)
        self.cursor_artists['time_label'] = time_axis.text(0, -.06, '',
            transform=time_axis.get_xaxis_transform(), ha='center', va='top', fontsize=8,
            color='white', bbox=label_style, animated=True, zorder=25, clip_on=False)
        visible = (self.cursor_mode and start_idx <= self.cursor_abs_idx <= end_idx)
        for artist in self.cursor_artists.values():
            artist.set_visible(visible)

    def _cache_cursor_backgrounds(self):
        if not self.cursor_artists:
            self._cursor_backgrounds.clear()
            return
        self._cursor_backgrounds = {
            self.fig: self.canvas.copy_from_bbox(self.fig.bbox)
        }

    def _on_chart_draw(self, event):
        """每次整图重绘（包括窗口缩放/DPI变化）重建不含光标的背景。"""
        self._cache_cursor_backgrounds()
        if self.cursor_artists and self.stock_data is not None:
            self._update_cursor_overlay()

    def _update_cursor_overlay(self):
        """仅更新四条光标线；常规鼠标移动不重新计算/绘制行情图。"""
        if not self.cursor_artists or not self._cursor_backgrounds or self.stock_data is None:
            self._draw_combined_chart()
            return
        start_idx, end_idx = self.display_start_idx, self.display_end_idx
        visible = (self.cursor_mode and start_idx <= self.cursor_abs_idx <= end_idx)
        if visible:
            local_x = self.cursor_abs_idx - start_idx
            close_y = self._cursor_price if self._cursor_price is not None else float(self.stock_data['Close'].iat[self.cursor_abs_idx])
            for key in ('kline_v', 'volume_v', 'macd_v'):
                self.cursor_artists[key].set_xdata([local_x, local_x])
            self.cursor_artists['kline_h'].set_ydata([close_y, close_y])
            self.cursor_artists['price_label'].set_position((1, close_y))
            self.cursor_artists['price_label'].set_text(f' {close_y:.2f} ')
            self.cursor_artists['time_label'].set_x(local_x)
            self.cursor_artists['time_label'].set_text(
                f'第 {self.cursor_abs_idx + 1} 根' if self.cb_hide_date.isChecked() else
                self.stock_data.index[self.cursor_abs_idx].strftime('%Y-%m-%d %H:%M'))
            self._update_chart_quote_at(self.cursor_abs_idx)
        elif 0 <= self.current_date_idx < len(self.stock_data):
            self._update_chart_quote_at(self.current_date_idx)
        for artist in self.cursor_artists.values():
            artist.set_visible(visible)

        axes_artists = {
            self.ax_kline: [self.cursor_artists['kline_v'], self.cursor_artists['kline_h'], self.cursor_artists['price_label']],
            self.ax_volume: [self.cursor_artists['volume_v']],
            self.ax_macd: [self.cursor_artists['macd_v']],
        }
        axes_artists[self.cursor_time_axis].append(self.cursor_artists['time_label'])
        background = self._cursor_backgrounds.get(self.fig)
        if background is None:
            self._draw_combined_chart()
            return
        # 整个画布一次恢复，避免分数像素轴边界残留，以及三次 blit 的中间帧。
        self.canvas.restore_region(background)
        for ax, artists in axes_artists.items():
            if visible and ax.get_visible():
                for artist in artists:
                    ax.draw_artist(artist)
        self.canvas.blit(self.fig.bbox)

    def _update_chart_quote_at(self, idx: int):
        """更新图表顶部的紧凑 OHLCV 行情信息。"""
        if self.stock_data is None or idx < 0 or idx >= len(self.stock_data):
            return
        row = self.stock_data.iloc[idx]
        previous = float(self.stock_data['Close'].iat[idx - 1]) if idx > 0 else float(row['Open'])
        change = float(row['Close']) - previous
        change_pct = change / previous * 100 if previous else 0.0
        timestamp = self.stock_data.index[idx]
        time_text = timestamp.strftime('%Y-%m-%d %H:%M')
        if self.cb_hide_date.isChecked():
            time_text = '时间隐藏'
        volume = float(row.get('Volume', 0))
        self.lbl_chart_quote.setText(
            f"{time_text}    O {float(row['Open']):.2f}   H {float(row['High']):.2f}   "
            f"L {float(row['Low']):.2f}   C {float(row['Close']):.2f}   "
            f"{change:+.2f} ({change_pct:+.2f}%)   VOL {volume:,.0f}")

    def toggle_cursor_mode(self):
        self.cursor_mode = not self.cursor_mode
        if self.cursor_mode:
            self.cursor_abs_idx = self.current_date_idx
            self.statusBar().showMessage("光标模式 · 鼠标或方向键查看K线，双击返回缩放", 5000)
        else:
            self.cursor_abs_idx = -1
            self.statusBar().showMessage("缩放模式 · 滚轮缩放，方向键平移", 5000)
        self._draw_combined_chart()

    def exit_cursor_mode(self):
        if self.cursor_mode:
            self.cursor_mode = False
            self.cursor_abs_idx = -1
            self._draw_combined_chart()

    # ========== 绘图方法 ==========
    def _draw_combined_chart(self):
        logger.debug(f"绘图开始: stock_data={self.stock_data is not None}, current_date_idx={self.current_date_idx}")
        if self.stock_data is None or self.current_date_idx < 0:
            logger.debug("绘图跳过：数据未就绪")
            return
        try:
            if self.view_mode == 'fenshi':
                self._clamp_display_to_day()
            max_idx = self.current_date_idx
            start_idx = max(0, min(self.display_start_idx, max_idx))
            end_idx = max(start_idx, min(self.display_end_idx, max_idx))
            if end_idx < start_idx:
                end_idx = start_idx

            data_len = end_idx - start_idx + 1
            if data_len < 1:
                return

            indicators = self._get_indicators_slice(start_idx, end_idx)
            if not indicators:
                return

            self.ax_kline.set_visible(True)
            show_volume = self.indicator_checks['成交量'].isChecked()
            show_macd = self.indicator_checks['MACD'].isChecked()
            self.ax_volume.set_visible(show_volume)
            self.ax_macd.set_visible(show_macd)

            self.ax_kline.clear()
            self.ax_volume.clear()
            self.ax_macd.clear()
            self.cursor_artists.clear()
            self._cursor_backgrounds.clear()
            self.ax_volume.sharex(self.ax_kline)
            self.ax_macd.sharex(self.ax_kline)

            self.volume_bars = None
            self.macd_bars = None

            if self.view_mode != 'fenshi':
                # 分时模式下不挂载K线蜡烛图集合（避免旧蜡烛残留在分时线上）
                if self.kline_lines is not None:
                    self.ax_kline.add_collection(self.kline_lines)
                if self.kline_rects is not None:
                    self.ax_kline.add_collection(self.kline_rects)

            self.open_lines = []
            self.close_lines = []

            for ax in [self.ax_kline, self.ax_volume, self.ax_macd]:
                ax.set_facecolor('#0D1116')
                ax.spines['top'].set_visible(False)
                ax.spines['right'].set_visible(False)
                ax.spines['bottom'].set_visible(False)
                ax.spines['left'].set_visible(False)
                ax.tick_params(axis='x', colors='#888888', labelsize=8)
                ax.tick_params(axis='y', colors='#888888', labelsize=8)
                ax.grid(True, alpha=0.22, color='#343A42', linewidth=0.6)
                ax.yaxis.tick_right()

            x = np.arange(data_len)

            if self.view_mode == 'fenshi':
                self._draw_fenshi_lines(x, indicators, start_idx)
            else:
                self._draw_kline_collections(x, indicators)
                if self.indicator_checks['MA'].isChecked():
                    self._draw_ma_lines(x, indicators)

            low_min = indicators['low'].min()
            high_max = indicators['high'].max()
            price_padding = max((high_max - low_min) * .07, high_max * .001, .01)
            self.ax_kline.set_ylim(low_min - price_padding, high_max + price_padding)
            if self.ax_kline.lines:
                self.ax_kline.legend(loc='upper left', fontsize=7, facecolor='#11151A',
                                      edgecolor='#30363D', labelcolor='linecolor')
            self.ax_kline.set_yticks(self.ax_kline.get_yticks()[1:-1])

            if show_volume:
                self._draw_volume_collections(x, indicators)
            if show_macd:
                self._draw_macd_collections(x, indicators)
            self._draw_annotations(start_idx, end_idx)
            current_price = self._get_current_price()
            self.ax_kline.axhline(current_price, color='#00A88C', linestyle=':', linewidth=.7)
            self.ax_kline.text(1, current_price, f' {current_price:.2f} ',
                transform=self.ax_kline.get_yaxis_transform(), color='white', fontsize=8,
                va='center', bbox={'facecolor': '#008C76', 'edgecolor': 'none'}, clip_on=False)

            self.ax_kline.set_xlim(-0.5, data_len - 0.5)
            count = int(show_volume) + int(show_macd)
            bottom = .075
            panel_height = .15 if count else 0
            for axis, enabled in [(self.ax_macd, show_macd), (self.ax_volume, show_volume)]:
                if enabled:
                    axis.set_position([.015, bottom, .915, panel_height])
                    bottom += panel_height + .025
            self.ax_kline.set_position([.015, bottom, .915, .975 - bottom])
            tick_axis = self.ax_macd if show_macd else self.ax_volume if show_volume else self.ax_kline
            ticks = np.unique(np.linspace(0, data_len - 1, min(6, data_len), dtype=int))
            labels = [f'第 {start_idx + int(i) + 1} 根' if self.cb_hide_date.isChecked()
                      else self.stock_data.index[start_idx + int(i)].strftime(
                          '%m-%d %H:%M' if self.current_period.endswith('min') else '%Y-%m-%d') for i in ticks]
            tick_axis.set_xticks(ticks, labels)
            for axis in (self.ax_kline, self.ax_volume, self.ax_macd):
                axis.tick_params(axis='x', labelbottom=axis is tick_axis)

            self._create_cursor_artists(start_idx, end_idx)
            self.canvas.draw()

        except Exception as e:
            logger.error(f"绘图错误: {e}", exc_info=True)
            QMessageBox.warning(self, "绘图错误", f"绘制K线图时出错：{str(e)}")

    def _get_indicators_slice(self, start_idx: int, end_idx: int) -> Dict[str, pd.Series]:
        df = self.stock_data
        if df is None or df.empty:
            return {}
        slice_df = df.iloc[start_idx:end_idx + 1]
        return {
            'close': slice_df['Close'],
            'high': slice_df['High'],
            'low': slice_df['Low'],
            'open': slice_df['Open'],
            'volume': slice_df.get('Volume', pd.Series(0, index=slice_df.index)),
            'ma5': slice_df['MA5'],
            'ma10': slice_df['MA10'],
            'ma20': slice_df['MA20'],
            'ma60': slice_df['MA60'],
            'dif': slice_df['DIF'],
            'dea': slice_df['DEA'],
            'macd_bar': slice_df['MACD']
        }

    def _reset_collections(self):
        self.kline_lines = None
        self.kline_rects = None
        self.ma_lines.clear()
        self.macd_lines.clear()
        self.macd_bars = None
        self.volume_bars = None
        self.open_lines.clear()
        self.close_lines.clear()

    def _draw_fenshi_lines(self, x, indicators, start_idx):
        """分时图主图：白色价格线 + 黄色均价线（当日累计VWAP）+ 昨收虚线参考线"""
        close = indicators['close'].values.astype(float)
        volume = indicators['volume'].values.astype(float)

        self.ax_kline.plot(x, close, color='#FFFFFF', linewidth=1.2,
                           label='分时价格', zorder=3)
        # 均价线 = 当日累计成交额 / 累计成交量（成交量单位不影响比值）
        cum_vol = np.cumsum(volume)
        avg = np.where(cum_vol > 0,
                       np.cumsum(close * volume) / np.maximum(cum_vol, 1e-9),
                       close)
        self.ax_kline.plot(x, avg, color='#FFD700', linewidth=1.0,
                           label='均价', zorder=2)
        # 昨收参考线：当前训练日第一根K线之前一根的收盘价
        if start_idx > 0:
            prev_close = float(self.stock_data.iloc[start_idx - 1]['Close'])
        else:
            prev_close = float(close[0])
        self.ax_kline.axhline(y=prev_close, color='#AAAAAA', linestyle='--',
                              linewidth=0.8, alpha=0.7)

    def _draw_kline_collections(self, x, indicators):
        from matplotlib.collections import LineCollection, PatchCollection
        from matplotlib.patches import Rectangle

        open_ = indicators['open']
        close = indicators['close']
        high = indicators['high']
        low = indicators['low']

        segments = []
        rects = []
        colors = []

        for i in range(len(x)):
            o = open_.iloc[i]
            c = close.iloc[i]
            h = high.iloc[i]
            l = low.iloc[i]
            color = config.UP_COLOR if c >= o else config.DOWN_COLOR

            segments.append([(x[i], l), (x[i], h)])

            if c >= o:
                bottom = o
                height = c - o
            else:
                bottom = c
                height = o - c
            rect = Rectangle(
                (x[i] - config.KLINE_WIDTH / 2, bottom),
                config.KLINE_WIDTH, height
            )
            rects.append(rect)
            colors.append(color)

        if self.kline_lines is None:
            self.kline_lines = LineCollection(segments, colors=colors, linewidths=1)
            self.ax_kline.add_collection(self.kline_lines)
        else:
            self.kline_lines.set_segments(segments)
            self.kline_lines.set_colors(colors)

        if self.kline_rects is None:
            self.kline_rects = PatchCollection(rects, facecolor=colors, edgecolor=colors, linewidth=0.8)
            self.ax_kline.add_collection(self.kline_rects)
        else:
            self.kline_rects.set_paths(rects)
            self.kline_rects.set_facecolor(colors)
            self.kline_rects.set_edgecolor(colors)

    def _draw_ma_lines(self, x, indicators):
        ma_periods = [5, 10, 20, 60]
        for period in ma_periods:
            key = f'ma{period}'
            if key not in indicators:
                continue
            y = indicators[key]
            if key not in self.ma_lines:
                line, = self.ax_kline.plot(x, y, color=config.MA_COLORS[period],
                                            linewidth=1.0, label=f'MA{period}')
                self.ma_lines[key] = line
            else:
                line = self.ma_lines[key]
                if line not in self.ax_kline.lines:
                    self.ax_kline.add_line(line)
                line.set_data(x, y)

    def _draw_volume_collections(self, x, indicators):
        volume = indicators['volume']
        close = indicators['close']
        open_ = indicators['open']

        if len(volume) == 0:
            return

        colors = [config.UP_COLOR if c >= o else config.DOWN_COLOR
                  for c, o in zip(close, open_)]

        self.volume_bars = self.ax_volume.bar(x, volume, width=config.VOLUME_WIDTH,
                                               color=colors, alpha=0.8)

        if volume.max() > 0:
            self.ax_volume.set_ylim(0, volume.max() * 1.5)
        else:
            self.ax_volume.set_ylim(0, 1)

        self.ax_volume.set_yticks([])

    def _draw_macd_collections(self, x, indicators):
        dif = indicators['dif']
        dea = indicators['dea']
        macd_bar = indicators['macd_bar']

        if 'dif' not in self.macd_lines:
            line, = self.ax_macd.plot(x, dif, color='#FFFFFF', linewidth=1.0, label='DIF')
            self.macd_lines['dif'] = line
        else:
            line = self.macd_lines['dif']
            if line not in self.ax_macd.lines:
                self.ax_macd.add_line(line)
            line.set_data(x, dif)

        if 'dea' not in self.macd_lines:
            line, = self.ax_macd.plot(x, dea, color='#FFFF00', linewidth=1.0, label='DEA')
            self.macd_lines['dea'] = line
        else:
            line = self.macd_lines['dea']
            if line not in self.ax_macd.lines:
                self.ax_macd.add_line(line)
            line.set_data(x, dea)

        for xi, bar in zip(x, macd_bar):
            if bar > 0:
                self.ax_macd.vlines(xi, 0, bar, colors=config.UP_COLOR, linewidth=1)
            else:
                self.ax_macd.vlines(xi, bar, 0, colors=config.DOWN_COLOR, linewidth=1)

        if self.ax_macd.lines:
            self.ax_macd.legend(loc='upper left', fontsize=7, facecolor='#11151A',
                                 edgecolor='#30363D', labelcolor='linecolor')
        self.ax_macd.set_yticks([])

    def _update_status_ui(self, reset: bool = False):
        if reset:
            self.lbl_cur_date.setText("未开始")
            self.lbl_cur_stock.setText("等待载入行情")
            self.lbl_cur_cash.setText("0.00 元")
            self.lbl_cur_hold.setText("0 股")
            self.lbl_total_asset.setText("¥ 0.00")
            self.lbl_avg_cost.setText("--")
            self.lbl_market_price.setText("--")
            self.lbl_unrealized.setText("0.00 元")
            self.lbl_realized.setText("0.00 元")
            self.lbl_profit_loss.setText("0.00 元  (0.00%)")
            self.lbl_profit_loss.setStyleSheet("font-weight: bold; color: #94A3B8;")
            self.lbl_unrealized.setStyleSheet("font-weight: bold; color: #94A3B8;")
            self.lbl_realized.setStyleSheet("font-weight: bold; color: #94A3B8;")
            self.lbl_control_position.setText("持仓 0 股")
            self.le_trade_price.clear()
            return

        if self.current_date and self.simulator.current_stock:
            if self.cb_hide_date.isChecked():
                show_date = "【隐藏】"
            else:
                if self.current_date.hour == 0 and self.current_date.minute == 0:
                    show_date = self.current_date.strftime('%Y-%m-%d')
                else:
                    show_date = self.current_date.strftime('%Y-%m-%d %H:%M')

            if self.cb_hide_stock.isChecked():
                show_stock = "【隐藏】"
            else:
                show_stock = f"{self.imported_stock_name}({self.imported_stock_code})"

            current_price = self._get_current_price()
            total_asset = self.simulator.get_total_asset(current_price)
            current_hold = self.simulator.get_current_hold()
            average_cost = self.simulator.get_average_cost()
            unrealized = self.simulator.get_unrealized_pnl(current_price)
            realized = self.simulator.get_realized_pnl()

            self.lbl_cur_date.setText(show_date)
            self.lbl_cur_stock.setText(show_stock)
            self.lbl_cur_cash.setText(f"¥ {self.simulator.current_capital_float:,.2f}")
            self.lbl_cur_hold.setText(f"{current_hold:,} 股")
            self.lbl_total_asset.setText(f"¥ {total_asset:,.2f}")
            self.lbl_avg_cost.setText(f"¥ {average_cost:.2f}" if current_hold else "--")
            self.lbl_market_price.setText(f"¥ {current_price:.2f}")
            self.lbl_unrealized.setText(f"{unrealized:+,.2f} 元")
            self.lbl_realized.setText(f"{realized:+,.2f} 元")
            sellable = self.simulator.get_sellable_hold(self.current_date)
            self.lbl_control_position.setText(f"持仓 {current_hold:,} 股 · 可卖 {sellable:,} 股")
            initial = float(self.simulator.initial_capital)
            profit = total_asset - initial
            return_pct = profit / initial * 100 if initial else 0.0
            profit_color = '#EF4444' if profit > 0 else '#10B981' if profit < 0 else '#94A3B8'
            self.lbl_profit_loss.setText(f"{profit:+,.2f} 元  ({return_pct:+.2f}%)")
            self.lbl_profit_loss.setStyleSheet(f"font-weight: bold; color: {profit_color};")
            unrealized_color = '#EF4444' if unrealized > 0 else '#10B981' if unrealized < 0 else '#94A3B8'
            realized_color = '#EF4444' if realized > 0 else '#10B981' if realized < 0 else '#94A3B8'
            self.lbl_unrealized.setStyleSheet(f"font-weight: bold; color: {unrealized_color};")
            self.lbl_realized.setStyleSheet(f"font-weight: bold; color: {realized_color};")
            self._update_chart_quote_at(self.current_date_idx)

            self.le_trade_price.setText(f"{current_price:.2f}")
            readonly = self.current_date_idx <= self.history_end_idx
            # 修复(2026-08-31)：此处不能调用 setReadOnly/setEnabled 切换输入框状态——
            # PyQt6 6.9.x 在周期切换的信号回调链中触发原生崩溃(0xC0000409，栈安全cookie)。
            # 改用样式提供「历史展示阶段」视觉提示；交易拦截由各交易处理函数的
            # 历史阶段校验（"当前处于历史展示阶段"警告）负责。
            self.le_trade_price.setStyleSheet(
                "color: #888888; background-color: #333333;" if readonly else
                "color: #E6E9ED; background-color: #0D1116;")

    def _update_trade_history(self):
        self.tbl_trade_hist.setRowCount(len(self.simulator.trade_history))

        for row, record in enumerate(reversed(self.simulator.trade_history)):
            if self.cb_hide_date.isChecked():
                date_str = "【隐藏】"
            else:
                if record.date.hour == 0 and record.date.minute == 0:
                    date_str = record.date.strftime('%Y-%m-%d')
                else:
                    date_str = record.date.strftime('%Y-%m-%d %H:%M')

            values = [
                date_str, record.action.value, f"{record.price:.2f}", f"{record.amount:,}",
                f"{record.fee:.2f}", f"{record.net_amount:+,.2f}",
                f"{record.realized_pnl:+,.2f}" if record.action.value == '卖出' else '—',
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column > 1:
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                if column == 1:
                    item.setForeground(QColor('#EF4444' if record.action.value == '买入' else '#10B981'))
                if column == 6 and record.action.value == '卖出':
                    item.setForeground(QColor('#EF4444' if record.realized_pnl > 0 else '#10B981'))
                self.tbl_trade_hist.setItem(row, column, item)
        self.tbl_trade_hist.scrollToTop()

    def _clear_trade_history(self):
        self.tbl_trade_hist.setRowCount(0)

    def _set_trade_buttons_enabled(self, enabled: bool):
        enabled = bool(enabled)
        self.btn_buy.setEnabled(enabled)
        self.btn_sell.setEnabled(enabled)
        self.btn_next.setEnabled(enabled)
        # 修复(2026-08-31)：不切换 le_trade_price 的 enabled 状态，改样式提示
        self.le_trade_price.setStyleSheet(
            "color: #888888; background-color: #333333;" if not enabled else
            "color: #E6E9ED; background-color: #0D1116;")
        self.sb_trade_amount.setEnabled(enabled)
        self.btn_qty_minus.setEnabled(enabled)
        self.btn_qty_plus.setEnabled(enabled)
        self.ratio_combo.setEnabled(enabled)

    def closeEvent(self, event):
        self.stop_playback()
        self._save_settings()
        event.accept()
