from __future__ import annotations

import json

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)


class FacebookPageManagementPage(QWidget):
    """Central UI for existing project-scoped Facebook connections."""
    connectRequested = Signal(str, str)
    testRequested = Signal(str)
    syncRequested = Signal(str)
    defaultRequested = Signal(str, str)
    disconnectRequested = Signal(str, str)

    def __init__(self, database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self._verified_pages: set[tuple[str, str]] = set()
        root = QVBoxLayout(self); root.setContentsMargins(28, 20, 28, 20); root.setSpacing(10)
        title = QLabel("Quản lý Fanpage"); title.setObjectName("title"); root.addWidget(title)
        root.addWidget(QLabel("Quản lý kết nối Facebook cục bộ. Access Token chỉ được lưu trong Windows Credential Manager."))
        card = QFrame(); card.setObjectName("facebookCard"); form = QFormLayout(card)
        self.project = QComboBox(); self.project.currentIndexChanged.connect(self.refresh_pages)
        self.token = QLineEdit(); self.token.setEchoMode(QLineEdit.EchoMode.Password); self.token.setPlaceholderText("Nhập User/Page Access Token")
        buttons = QHBoxLayout()
        self.connect_button = QPushButton("Kết nối Fanpage"); self.connect_button.setObjectName("primary"); self.connect_button.clicked.connect(self._connect)
        self.test_button = QPushButton("Kiểm tra Token & quyền"); self.test_button.clicked.connect(self._test)
        self.sync_button = QPushButton("Đồng bộ Page"); self.sync_button.clicked.connect(self._sync)
        for button in (self.connect_button, self.test_button, self.sync_button): buttons.addWidget(button)
        buttons.addStretch()
        form.addRow("Dự án", self.project); form.addRow("Access Token", self.token); form.addRow(buttons)
        root.addWidget(card)
        self.status = QLabel("Chọn dự án để xem các Fanpage đã kết nối."); self.status.setWordWrap(True); root.addWidget(self.status)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Mặc định", "Tên Fanpage", "Page ID", "Kết nối", "Đăng video", "Đọc bài viết", "Quản lý comment", "Thao tác"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True); self.table.setColumnWidth(0, 82); self.table.setColumnWidth(2, 170)
        root.addWidget(self.table, 1)
        self.refresh_projects()

    def refresh_projects(self) -> None:
        current = self.project.currentData(); self.project.blockSignals(True); self.project.clear(); self.project.addItem("Chọn dự án", None)
        for row in self.database.list_projects(): self.project.addItem(row["name"], row["uuid"])
        self.project.setCurrentIndex(max(0, self.project.findData(current))); self.project.blockSignals(False); self.refresh_pages()

    def refresh_pages(self, *_args) -> None:
        project_id = self.project.currentData(); self.table.setRowCount(0)
        if not project_id:
            self.status.setText("Chọn dự án để xem các Fanpage đã kết nối."); return
        rows = list(self.database.connection.execute(
            "SELECT * FROM facebook_pages WHERE project_id=? ORDER BY page_name COLLATE NOCASE", (project_id,)
        ))
        if not rows:
            self.status.setText("Chưa có Fanpage nào trong dữ liệu cục bộ. Kết nối token để tải danh sách Page."); return
        self.status.setText(f"Đã tải {len(rows)} Fanpage cục bộ. Quyền chỉ được xác minh sau khi kiểm tra token.")
        for page in rows:
            row = self.table.rowCount(); self.table.insertRow(row); page_id = str(page["page_id"])
            default = QTableWidgetItem("✓" if page["is_default"] else "")
            self.table.setItem(row, 0, default); self.table.setItem(row, 1, QTableWidgetItem(str(page["page_name"]))); self.table.setItem(row, 2, QTableWidgetItem(page_id))
            read_status = str(page["read_access_status"] or "UNVERIFIED")
            verified = read_status == "GRANTED"
            connection_label = {"GRANTED": "Đã xác minh", "TOKEN_MISSING": "Thiếu token", "TOKEN_EXPIRED": "Token hết hạn/thu hồi", "WRONG_PAGE": "Sai Fanpage", "DENIED": "Bị từ chối quyền", "NETWORK_ERROR": "Lỗi mạng", "API_ERROR": "Lỗi API"}.get(read_status, "Đã kết nối (chưa xác minh)")
            self.table.setItem(row, 3, QTableWidgetItem(connection_label))
            permissions = set(json.loads(page["permissions_json"] or "[]"))
            self.table.setItem(row, 4, QTableWidgetItem("Có" if verified and "pages_manage_posts" in permissions else "Chưa xác minh"))
            read_ok = "pages_read_engagement" in permissions
            comment_ok = "pages_manage_engagement" in permissions
            self.table.setItem(row, 5, QTableWidgetItem("Có" if verified else "Chưa xác minh"))
            self.table.setItem(row, 6, QTableWidgetItem("Có" if verified and comment_ok else "Chưa xác minh"))
            actions = QWidget(); action_layout = QHBoxLayout(actions); action_layout.setContentsMargins(2, 1, 2, 1)
            make_default = QPushButton("Mặc định"); make_default.setEnabled(not bool(page["is_default"])); make_default.clicked.connect(lambda _=False, p=page_id: self.defaultRequested.emit(str(project_id), p))
            disconnect = QPushButton("Ngắt"); disconnect.setObjectName("danger"); disconnect.clicked.connect(lambda _=False, p=page_id, n=str(page["page_name"]): self._disconnect(p, n))
            action_layout.addWidget(make_default); action_layout.addWidget(disconnect); self.table.setCellWidget(row, 7, actions)

    def set_verified(self, project_id: str, pages: list[dict]) -> None:
        self._verified_pages.update((project_id, str(page.get("id", ""))) for page in pages)
        self.status.setText(f"Đã xác minh {len(pages)} Fanpage từ Facebook."); self.refresh_pages()

    def set_status(self, message: str) -> None: self.status.setText(message)

    def _connect(self) -> None:
        if not self.project.currentData() or not self.token.text().strip(): self.set_status("Chọn dự án và nhập Access Token."); return
        self.connectRequested.emit(str(self.project.currentData()), self.token.text())

    def _test(self) -> None:
        if self.project.currentData(): self.testRequested.emit(str(self.project.currentData()))
        else: self.set_status("Hãy chọn dự án trước.")

    def _sync(self) -> None:
        if self.project.currentData(): self.syncRequested.emit(str(self.project.currentData()))
        else: self.set_status("Hãy chọn dự án trước.")

    def _disconnect(self, page_id: str, page_name: str) -> None:
        if QMessageBox.question(self, "Ngắt kết nối Fanpage", f"Ngắt kết nối {page_name}? Token của Page này sẽ bị xóa khỏi Windows Credential Manager.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.disconnectRequested.emit(str(self.project.currentData()), page_id)
