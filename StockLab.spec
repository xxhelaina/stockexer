# Build with: .venv-release/Scripts/python.exe -m PyInstaller StockLab.spec
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

datas = copy_metadata('baostock')
hiddenimports = collect_submodules('baostock', filter=lambda name: '.demo' not in name)
a = Analysis(
    ['main.py'], pathex=[], binaries=[], datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={'matplotlib': {'backends': ['QtAgg']}},
    runtime_hooks=[], excludes=['tkinter', 'PySide6', 'PyQt5', 'IPython', 'pytest', 'scipy'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='StockLab',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='StockLab')
