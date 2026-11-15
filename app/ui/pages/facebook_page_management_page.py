from __future__ import annotations

import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from app.services.credential_service import get_page_token


class FacebookPageManagementPage(QWidget):
    """Application-scoped Facebook Page connection management."""
    connectRequested = Signal(str)
    testRequested = Signal()
    syncRequested = Signal()
    disconnectRequested = Signal(str)

    def __init__(self, database, parent: QWidget | None = None) -> None:
        super().__init__(parent); self.database = database
        self.project = QComboBox()  # Compatibility only; intentionally not added to the UI.
        for project in self.database.list_projects(): self.project.addItem(project["name"], project["uuid"])
        root = QVBoxLayout(self); root.setContentsMargins(28, 20, 28, 20); root.setSpacing(10)
        title = QLabel("Quản lý Fanpage"); title.setObjectName("title"); root.addWidget(title)
        root.addWidget(QLabel("Quản lý kết nối Facebook tập trung. Access Token chỉ được lưu trong Windows Credential Manager."))
        card = QFrame(); card.setObjectName("facebookCard"); form = QFormLayout(card)
        self.token = QLineEdit(); self.token.setMinimumWidth(420); self.token.setEchoMode(QLineEdit.EchoMode.Password); self.token.setPlaceholderText("Nhập User/Page Access Token")
        buttons = QHBoxLayout(); self.connect_button = QPushButton("Kết nối Fanpage"); self.connect_button.setObjectName("primary"); self.connect_button.clicked.connect(self._connect)
        self.test_button = QPushButton("Kiểm tra Token & quyền"); self.test_button.clicked.connect(self._test)
        self.sync_button = QPushButton("Đồng bộ Page"); self.sync_button.clicked.connect(self._sync)
        for button in (self.connect_button, self.test_button, self.sync_button): buttons.addWidget(button)
        buttons.addStretch(); form.addRow("Access Token", self.token); form.addRow(buttons); root.addWidget(card)
        self.status = QLabel(); self.status.setWordWrap(True); root.addWidget(self.status)
        self.table = QTableWidget(0, 9); self.table.setHorizontalHeaderLabels(["Tên Fanpage", "Page ID", "Trạng thái Token", "Hạn Token", "Đăng video", "Đọc bài viết", "Quản lý comment", "Kiểm tra gần nhất", "Thao tác"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents); self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1); self.refresh_pages()

    def refresh_projects(self) -> None:
        self.refresh_pages()

    def refresh_pages(self, *_args) -> None:
        self.table.setRowCount(0); rows = []
        for row in self.database.connection.execute("SELECT * FROM facebook_page_connections ORDER BY page_name COLLATE NOCASE"):
            legacy = self.database.connection.execute("SELECT project_id FROM facebook_pages WHERE page_id=? LIMIT 1", (row["page_id"],)).fetchone()
            if get_page_token(str(row["page_id"])) or (legacy and get_page_token(str(legacy["project_id"]), str(row["page_id"]))): rows.append(row)
        self.status.setText(f"Đã tải {len(rows)} Fanpage kết nối tập trung." if rows else "Chưa có Fanpage nào. Kết nối token để tải danh sách Page.")
        for page in rows:
            row = self.table.rowCount(); self.table.insertRow(row); page_id = str(page["page_id"]); status = str(page["token_status"] or "UNKNOWN")
            permissions = set(json.loads(page["permissions_json"] or "[]")); valid = status == "VALID"
            values = [page["page_name"], page_id, status, page["token_expires_at"] or "Không xác định", "Có" if valid and "pages_manage_posts" in permissions else "Chưa xác minh", "Có" if valid and "pages_read_engagement" in permissions else "Chưa xác minh", "Có" if valid and "pages_manage_engagement" in permissions else "Chưa xác minh", page["last_validated_at"] or "Chưa kiểm tra"]
            for column, value in enumerate(values): self.table.setItem(row, column, QTableWidgetItem(str(value)))
            actions = QWidget(); layout = QHBoxLayout(actions); layout.setContentsMargins(2, 1, 2, 1)
            check = QPushButton("Kiểm tra lại"); check.clicked.connect(self.testRequested.emit)
            disconnect = QPushButton("Ngắt"); disconnect.setObjectName("danger"); disconnect.clicked.connect(lambda _=False, p=page_id, n=str(page["page_name"]): self._disconnect(p, n))
            layout.addWidget(check); layout.addWidget(disconnect); self.table.setCellWidget(row, 8, actions)

    def set_verified(self, *args) -> None:
        pages = args[-1] if args else []
        self.status.setText(f"Đã xác minh {len(pages)} Fanpage từ Facebook."); self.refresh_pages()

    def set_status(self, message: str) -> None: self.status.setText(message)
    def _connect(self) -> None:
        if not self.token.text().strip(): self.set_status("Hãy nhập Access Token."); return
        self.connectRequested.emit(self.token.text())
    def _test(self) -> None: self.testRequested.emit()
    def _sync(self) -> None: self.syncRequested.emit()
    def _disconnect(self, page_id: str, page_name: str) -> None:
        if QMessageBox.question(self, "Ngắt kết nối Fanpage", f"Ngắt kết nối {page_name}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes: self.disconnectRequested.emit(page_id)
