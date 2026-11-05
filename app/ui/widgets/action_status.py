from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QFrame

from app.ui.widgets.loading_button import LoadingSpinner


class ActionStatusBar(QFrame):
    """Compact, reusable status surface for a background action."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("actionStatus")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        self.spinner = LoadingSpinner(28, self)
        layout.addWidget(self.spinner)
        text_layout = QHBoxLayout()
        text_layout.setSpacing(18)
        self.title = QLabel("Đang xử lý…")
        self.title.setObjectName("actionStatusTitle")
        self.detail = QLabel()
        self.detail.setObjectName("actionStatusDetail")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        text_layout.addWidget(self.title)
        text_layout.addWidget(self.detail, 1)
        layout.addLayout(text_layout, 1)
        self.hide()

    def set_loading(self, loading: bool) -> None:
        if loading:
            self.show()
            self.spinner.set_running(True)
        else:
            self.spinner.set_running(False)
            self.hide()

    def set_status(self, title: str, detail: str = "") -> None:
        self.title.setText(title)
        self.detail.setText(detail)

