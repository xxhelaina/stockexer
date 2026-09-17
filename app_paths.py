"""Persistent writable paths, independent of PyInstaller's resource directory."""
import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path


def application_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _writable_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=path):
        pass
    return path


@lru_cache(maxsize=1)
def data_dir():
    if not getattr(sys, 'frozen', False):
        return application_dir()
    try:
        return _writable_directory(application_dir() / 'data')
    except OSError:
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local' / 'share')
        return _writable_directory(base / 'StockLab')


def download_dir():
    return data_dir() / '下载数据'


def local_search_roots():
    return list(dict.fromkeys(map(str, (application_dir(), data_dir(), download_dir()))))
