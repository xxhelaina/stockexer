import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from gui.main_window import StockDoubleBlindTrainer

def main():
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
    main()
