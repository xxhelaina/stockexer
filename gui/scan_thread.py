# -*- coding: utf-8 -*-
from PyQt6.QtCore import QThread, pyqtSignal
import os
import re
from data_loader import is_valid_kline_file, load_stock_names

class ScanFolderThread(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(dict, dict)
    error = pyqtSignal(str)

    def __init__(self, folder_path):
        super().__init__()
        self.folder_path = folder_path

    def run(self):
        try:
            stock_files = {}
            all_files = []

            for root, dirs, files in os.walk(self.folder_path):
                for f in files:
                    f_lower = f.lower()
                    if f_lower.endswith('.txt') or f_lower.endswith('.day'):
                        all_files.append((root, f))

            total = len(all_files)
            if total == 0:
                self.error.emit("未找到任何 .txt 或 .day 文件")
                return

            for idx, (root, f) in enumerate(all_files):
                if self.isInterruptionRequested():
                    return
                f_lower = f.lower()
                full_path = os.path.join(root, f)
                if f_lower.endswith('.txt'):
                    if is_valid_kline_file(full_path):
                        rel_path = os.path.relpath(full_path, self.folder_path)
                        stock_files[rel_path] = (full_path, None, 'txt')
                elif f_lower.endswith('.day'):
                    rel_path = os.path.relpath(full_path, self.folder_path)
                    stock_files[rel_path] = (full_path, None, 'day')

                self.progress.emit(idx + 1, total)

            for rel_path, (full_path, _, ftype) in stock_files.items():
                base = os.path.basename(full_path)
                code_match = re.search(r'(\d{6})', base)
                stock_code = code_match.group(1) if code_match else None
                stock_files[rel_path] = (full_path, stock_code, ftype)

            stock_names = load_stock_names(self.folder_path)

            self.finished.emit(stock_files, stock_names)

        except Exception as e:
            self.error.emit(str(e))