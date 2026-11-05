from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class FacebookRequestWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self._operation = operation

    def run(self) -> None:
        try:
            self.succeeded.emit(self._operation())
        except Exception as exc:
            self.failed.emit(str(exc))
