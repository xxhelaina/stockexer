# -*- coding: utf-8 -*-
from PyQt6.QtCore import QThread, pyqtSignal


class DownloadThread(QThread):
    """在线下载K线数据的后台线程（多数据源自动切换）。"""
    finished = pyqtSignal(object, object)   # (df, meta_dict)
    error = pyqtSignal(str)

    def __init__(self, code: str, period_key: str, beg: str, end: str, fqt: int = 1):
        super().__init__()
        self.code = code
        self.period_key = period_key
        self.beg = beg
        self.end = end
        self.fqt = fqt

    def run(self):
        try:
            from online_data import download_kline
            df, name, source = download_kline(
                self.code, self.period_key, self.beg, self.end, self.fqt)
            if df is None or df.empty:
                self.error.emit('下载结果为空，请检查股票代码或日期范围')
                return
            meta = {
                'name': name or self.code,
                'source': source,
                'rows': len(df),
                'first': df.index[0],
                'last': df.index[-1],
            }
            self.finished.emit(df, meta)
        except Exception as e:
            self.error.emit(f'下载失败：{str(e)}')
