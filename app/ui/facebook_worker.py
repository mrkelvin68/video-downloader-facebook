from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from app.services.logging_service import log_app, log_error


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
            log_error(f"FacebookRequestWorker error: {exc}", exc=exc)
            self.failed.emit(str(exc))


class FacebookPublishWorker(QThread):
    progressChanged = Signal(float)
    stageChanged = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[Callable[[float], None]], Any], parent=None) -> None:
        super().__init__(parent)
        self._operation = operation

    def run(self) -> None:
        try:
            self.succeeded.emit(self._operation(self.progressChanged.emit))
        except Exception as exc:
            log_error(f"FacebookPublishWorker error: {exc}", exc=exc)
            self.failed.emit(str(exc))
