from PyQt6.QtCore import QThread, pyqtSignal

from training_data import resolve_training_data, LocalDataMissing


class AutoDataThread(QThread):
    resolved = pyqtSignal(object, object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)
    missing = pyqtSignal(str)

    def __init__(self, request, roots, paths, memory, allow_online=False):
        super().__init__()
        self.request = request
        self.roots = roots
        self.paths = paths
        self.memory = memory
        self.allow_online = allow_online

    def run(self):
        try:
            frame, meta = resolve_training_data(self.request, self.roots, self.paths, self.memory,
                self.progress.emit, self.isInterruptionRequested, allow_online=self.allow_online)
            if not self.isInterruptionRequested():
                self.resolved.emit(frame, meta)
        except InterruptedError:
            pass
        except LocalDataMissing as error:
            if not self.isInterruptionRequested():
                self.missing.emit(str(error))
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))
