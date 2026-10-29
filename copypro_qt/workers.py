"""Background tasks. The UI thread only receives finished results."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Signals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class _Call(QRunnable):
    def __init__(self, fn, signals):
        super().__init__()
        self.fn = fn
        self.signals = signals

    @Slot()
    def run(self):
        try:
            self.signals.finished.emit(self.fn())
        except Exception as exc:
            self.signals.failed.emit(str(exc))


def run_bg(fn, on_done, on_fail=None, parent=None):
    """Run fn off the UI thread. Keep the returned signals object alive."""
    signals = _Signals(parent)
    signals.finished.connect(on_done)
    if on_fail is not None:
        signals.failed.connect(on_fail)
    QThreadPool.globalInstance().start(_Call(fn, signals))
    return signals
