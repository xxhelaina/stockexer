import sys
import multiprocessing

def main():
    if '--self-test' in sys.argv:
        import argparse
        from release_smoke_test import run
        parser = argparse.ArgumentParser()
        parser.add_argument('--self-test', required=True)
        parser.add_argument('--network', action='store_true')
        args = parser.parse_args()
        return run(args.self_test, network=args.network)
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFont
    from gui.main_window import StockDoubleBlindTrainer
    app = QApplication(sys.argv)
    app.setApplicationName('StockLab')
    app.setApplicationDisplayName('StockLab · A股复盘训练')
    app.setOrganizationName('StockLab')
    app.setStyle('Fusion')
    font = QFont()
    font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "Segoe UI"])
    font.setPointSize(9)
    app.setFont(font)

    window = StockDoubleBlindTrainer()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    # Windowed EXEs have no stdout/stderr; BaoStock prints login messages.
    if getattr(sys, 'frozen', False):
        import os
        if sys.stdout is None:
            sys.stdout = open(os.devnull, 'w')
        if sys.stderr is None:
            sys.stderr = open(os.devnull, 'w')
    multiprocessing.freeze_support()
    sys.exit(main())
