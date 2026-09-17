# -*- coding: utf-8 -*-
from PyQt6.QtCore import QThread, pyqtSignal
import pandas as pd

class PrecomputeIndicatorsThread(QThread):
    finished = pyqtSignal(pd.DataFrame)
    progress = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, df: pd.DataFrame):
        super().__init__()
        self.df = df.copy()

    def run(self):
        try:
            if self.df is None or self.df.empty:
                self.error.emit("数据为空")
                return

            df = self.df
            close = df['Close']

            df['MA5'] = close.rolling(window=5, min_periods=1).mean()
            self.progress.emit(20)
            if self.isInterruptionRequested(): return

            df['MA10'] = close.rolling(window=10, min_periods=1).mean()
            self.progress.emit(40)
            if self.isInterruptionRequested(): return

            df['MA20'] = close.rolling(window=20, min_periods=1).mean()
            self.progress.emit(60)
            if self.isInterruptionRequested(): return

            df['MA60'] = close.rolling(window=60, min_periods=1).mean()
            df['MA120'] = close.rolling(window=120, min_periods=1).mean()
            df['MA250'] = close.rolling(window=250, min_periods=1).mean()
            volume = df.get('Volume', pd.Series(0.0, index=df.index))
            df['VOLMA5'] = volume.rolling(5, min_periods=1).mean()
            df['VOLMA10'] = volume.rolling(10, min_periods=1).mean()
            self.progress.emit(80)
            if self.isInterruptionRequested(): return

            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            df['DIF'] = ema12 - ema26
            df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
            df['MACD'] = 2 * (df['DIF'] - df['DEA'])
            self.progress.emit(100)

            self.finished.emit(df)

        except Exception as e:
            import traceback
            self.error.emit(f"指标计算失败: {str(e)}\n{traceback.format_exc()}")
