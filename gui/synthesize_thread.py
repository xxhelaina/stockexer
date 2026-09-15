# -*- coding: utf-8 -*-
from PyQt6.QtCore import QThread, pyqtSignal
import pandas as pd

class SynthesizePeriodThread(QThread):
    finished = pyqtSignal(object, str)
    error = pyqtSignal(str)

    def __init__(self, df_min1: pd.DataFrame, period_key: str):
        super().__init__()
        self.df_min1 = df_min1.copy()
        self.period_key = period_key

    def run(self):
        try:
            df = self.df_min1
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            df = df.sort_index()

            rule_map = {
                '5min': '5min',
                '15min': '15min',
                '20min': '20min',
                '30min': '30min',
                '60min': '60min',
                'D': 'D',
                'W': 'W',
                'M': 'M'
            }
            rule = rule_map.get(self.period_key)
            if not rule:
                raise ValueError(f"未知周期: {self.period_key}")

            agg_dict = {
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }
            df_resampled = df.resample(rule).agg(agg_dict)

            # 删除价格列全为 NaN 的行（例如无交易的周期）
            df_resampled.dropna(subset=['Open', 'High', 'Low', 'Close'], how='any', inplace=True)

            if self.period_key == 'W':
                df_resampled.index = df_resampled.index + pd.Timedelta(days=4)
            elif self.period_key == 'M':
                df_resampled.index = df_resampled.index + pd.offsets.MonthEnd(0)

            self.finished.emit(df_resampled, self.period_key)

        except Exception as e:
            self.error.emit(str(e))