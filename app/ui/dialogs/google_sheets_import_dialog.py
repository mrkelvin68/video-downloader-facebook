from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QDialogButtonBox,
    QVBoxLayout,
)

from app.services.google_sheets_service import fetch_google_sheet_csv, parse_google_sheet_csv
from app.ui.pages.viral_reels_page import CancellableWorker
from app.ui.widgets.action_status import ActionStatusBar
from app.ui.widgets.loading_button import LoadingButton


class GoogleSheetsImportDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nhập danh sách video từ Google Sheets")
        self.resize(620, 460)
        self.rows: list[dict[str, object]] = []
        self.csv_text = ""
        self.errors: list[str] = []
        self._worker: CancellableWorker | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://docs.google.com/spreadsheets/d/...")
        self.sheet_edit = QLineEdit()
        self.sheet_edit.setPlaceholderText("Để trống = tab mặc định; nhập tên tab nếu cần")
        form.addRow("Google Sheets URL", self.url_edit)
        form.addRow("Sheet/Tab", self.sheet_edit)
        layout.addLayout(form)

        self.preview_button = LoadingButton("Preview dữ liệu")
        self.preview_button.clicked.connect(self.preview)
        layout.addWidget(self.preview_button)
        self.action_status = ActionStatusBar(self)
        self.action_status.set_status("Đang đọc Google Sheets…")
        layout.addWidget(self.action_status)
        self.summary = QLabel("Chưa tải dữ liệu.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setPlaceholderText("Preview sẽ hiển thị ở đây.")
        layout.addWidget(self.preview_text, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_button = QPushButton("Import")
        self.import_button.setObjectName("primary")
        self.import_button.setEnabled(False)
        buttons.addButton(self.import_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        self.import_button.clicked.connect(self.accept)
        layout.addWidget(buttons)

    def preview(self) -> None:
        if self._worker is not None:
            return
        url = self.url_edit.text().strip()
        sheet = self.sheet_edit.text().strip()
        if not url:
            self.summary.setText("Vui lòng nhập Google Sheets URL.")
            return
        self.preview_button.set_loading(True)
        self.action_status.set_status("Đang đọc Google Sheets…", "Đang kết nối")
        self.action_status.set_loading(True)
        self.import_button.setEnabled(False)
        self.summary.setText("Đang đọc Google Sheets…")
        worker = CancellableWorker(lambda _cancel, _progress: self._load(url, sheet))
        self._worker = worker
        worker.signals.result.connect(self._show_preview)
        worker.signals.error.connect(self._show_error)
        worker.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(worker)

    @staticmethod
    def _load(url: str, sheet: str) -> tuple[str, list[dict[str, object]], list[str], dict[str, int]]:
        text = fetch_google_sheet_csv(url, sheet)
        return parse_google_sheet_csv(text)

    def _show_preview(self, payload: Any) -> None:
        self.csv_text, self.rows, self.errors, stats = payload
        self.summary.setText(
            f"Tổng dòng: {stats['total']} · URL hợp lệ: {stats['valid']} · "
            f"URL không hợp lệ: {stats['invalid']} · URL trùng: {stats['duplicates']} · "
            f"Sẽ thêm: {len(self.rows) if not self.errors else 0}"
        )
        preview_rows = [f"{row['ten_video']} | {row['url']}" for row in self.rows[:30]]
        if self.errors:
            preview_rows.append("\nLỗi theo dòng:")
            preview_rows.extend(self.errors[:30])
        self.preview_text.setPlainText("\n".join(preview_rows))
        self.import_button.setEnabled(bool(self.rows) and not self.errors)

    def _show_error(self, message: str) -> None:
        self.rows = []
        self.csv_text = ""
        self.errors = [message]
        self.summary.setText(message)
        self.preview_text.clear()
        self.import_button.setEnabled(False)

    def _finished(self) -> None:
        self._worker = None
        self.preview_button.set_loading(False)
        self.action_status.set_loading(False)

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._worker.cancel()
        super().closeEvent(event)
