from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


_PAGE_MANAGEMENT_STYLE = (
    "QTableWidget#facebookPageManagementTable{background:#0c1729;alternate-background-color:#101c30;"
    "border:1px solid #284365;color:#e2e8f0;gridline-color:#1c2d46;selection-background-color:#1d3c66;outline:none;}"
    "QTableWidget#facebookPageManagementTable::item{padding:6px 10px;outline:none;border:none;}"
    "QPushButton#pageActionCheck{background:#152844;border:1px solid #2f5485;border-radius:6px;"
    "color:#e0f2fe;font-size:12px;font-weight:600;padding:5px 12px;min-height:26px;}"
    "QPushButton#pageActionCheck:hover:enabled{background:#1e3a63;border-color:#60a5fa;color:#ffffff;}"
    "QPushButton#pageActionDisconnect{background:#2b1924;border:1px solid #6b2639;border-radius:6px;"
    "color:#fda4af;font-size:12px;font-weight:600;padding:5px 12px;min-height:26px;}"
    "QPushButton#pageActionDisconnect:hover:enabled{background:#451a2b;border-color:#f43f5e;color:#ffffff;}"
    "QPushButton#pageActionDelete{background:#3b1219;border:1px solid #991b1b;border-radius:6px;"
    "color:#fecaca;font-size:12px;font-weight:600;padding:5px 12px;min-height:26px;}"
    "QPushButton#pageActionDelete:hover:enabled{background:#7f1d1d;border-color:#ef4444;color:#ffffff;}"
)

_STATUS_LABELS = {
    "VALID": "Đã xác minh",
    "GRANTED": "Đã xác minh",
    "EXPIRED": "Token hết hạn/thu hồi",
    "TOKEN_EXPIRED": "Token hết hạn/thu hồi",
    "PERMISSION_MISSING": "Thiếu quyền",
    "DENIED": "Thiếu quyền",
    "CHECK_FAILED": "Lỗi kiểm tra",
    "API_ERROR": "Lỗi kiểm tra",
    "NETWORK_ERROR": "Lỗi mạng",
    "TOKEN_MISSING": "Thiếu token",
    "UNKNOWN": "Đã kết nối (chưa xác minh)",
    "UNVERIFIED": "Đã kết nối (chưa xác minh)",
}


class FacebookPageManagementPage(QWidget):
    """Application-scoped Facebook Page connection management."""

    connectRequested = Signal(str)
    testRequested = Signal()
    syncRequested = Signal()
    disconnectRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self.setStyleSheet(_PAGE_MANAGEMENT_STYLE)
        self.project = QComboBox()  # Compatibility only; intentionally not added to the UI.
        for project in self.database.list_projects():
            self.project.addItem(project["name"], project["uuid"])

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 28, 20)
        root.setSpacing(10)

        title = QLabel("Quản lý Fanpage")
        title.setObjectName("title")
        root.addWidget(title)
        root.addWidget(
            QLabel(
                "Quản lý kết nối Facebook tập trung. Access Token chỉ được lưu trong Windows Credential Manager."
            )
        )

        card = QFrame()
        card.setObjectName("facebookCard")
        form = QFormLayout(card)
        self.token = QLineEdit()
        self.token.setMinimumWidth(420)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Nhập User/Page Access Token")

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.connect_button = QPushButton("Kết nối Fanpage")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(self._connect)
        self.test_button = QPushButton("Kiểm tra Token & quyền")
        self.test_button.setObjectName("secondary")
        self.test_button.clicked.connect(self._test)
        self.sync_button = QPushButton("Đồng bộ Page")
        self.sync_button.setObjectName("secondary")
        self.sync_button.clicked.connect(self._sync)
        for button in (self.connect_button, self.test_button, self.sync_button):
            buttons.addWidget(button)
        buttons.addStretch()
        form.addRow("Access Token", self.token)
        form.addRow(buttons)
        root.addWidget(card)

        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.table = QTableWidget(0, 9)
        self.table.setObjectName("facebookPageManagementTable")
        self.table.setHorizontalHeaderLabels(
            [
                "Tên Fanpage",
                "Page ID",
                "Trạng thái Token",
                "Hạn Token",
                "Đăng video",
                "Đọc bài viết",
                "Quản lý comment",
                "Kiểm tra gần nhất",
                "Thao tác",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(44)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 155)
        self.table.setColumnWidth(2, 165)
        self.table.setColumnWidth(3, 155)
        self.table.setColumnWidth(4, 105)
        self.table.setColumnWidth(5, 105)
        self.table.setColumnWidth(6, 120)
        self.table.setColumnWidth(7, 145)
        root.addWidget(self.table, 1)
        self.refresh_pages()

    def refresh_projects(self) -> None:
        current = self.project.currentData()
        self.project.blockSignals(True)
        self.project.clear()
        for project in self.database.list_projects():
            self.project.addItem(project["name"], project["uuid"])
        if current is not None:
            idx = self.project.findData(current)
            if idx >= 0:
                self.project.setCurrentIndex(idx)
        self.project.blockSignals(False)
        self.refresh_pages()

    def refresh_pages(self, *_args) -> None:
        self.table.setRowCount(0)
        rows = [
            dict(row)
            for row in self.database.connection.execute(
                "SELECT * FROM facebook_page_connections ORDER BY page_name COLLATE NOCASE"
            ).fetchall()
        ]
        self.status.setText(
            f"Đã tải {len(rows)} Fanpage kết nối tập trung."
            if rows
            else "Chưa có Fanpage nào. Kết nối token để tải danh sách Page."
        )
        for page in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 44)
            page_id = str(page["page_id"])
            raw_status = str(page["token_status"] or "UNKNOWN")
            legacy = self.database.connection.execute(
                "SELECT read_access_status, permissions_json FROM facebook_pages WHERE page_id=? ORDER BY updated_at DESC LIMIT 1",
                (page_id,),
            ).fetchone()
            if raw_status == "UNKNOWN" and legacy and legacy["read_access_status"]:
                raw_status = str(legacy["read_access_status"])

            status_label = _STATUS_LABELS.get(raw_status, raw_status)
            valid = raw_status in {"VALID", "GRANTED"}
            raw_perms = page["permissions_json"] or (
                legacy["permissions_json"] if legacy else "[]"
            )
            permissions = set(json.loads(raw_perms or "[]"))

            expiry_display = (
                page["token_expires_at"]
                if page["token_expires_at"]
                else status_label
            )
            values = [
                page["page_name"],
                page_id,
                status_label,
                expiry_display,
                "Có" if valid and "pages_manage_posts" in permissions else "Chưa xác minh",
                "Có"
                if valid and ("pages_read_engagement" in permissions or valid)
                else "Chưa xác minh",
                "Có"
                if valid and "pages_manage_engagement" in permissions
                else "Chưa xác minh",
                page["last_validated_at"] or "Chưa kiểm tra",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)

            actions = QWidget()
            layout = QHBoxLayout(actions)
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(6)
            layout.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

            check = QPushButton("Kiểm tra lại")
            check.setObjectName("pageActionCheck")
            check.setCursor(Qt.CursorShape.PointingHandCursor)
            check.setToolTip("Kiểm tra lại trạng thái Token và quyền của Fanpage")
            check.clicked.connect(self.testRequested.emit)

            disconnect = QPushButton("Ngắt kết nối")
            disconnect.setObjectName("pageActionDisconnect")
            disconnect.setCursor(Qt.CursorShape.PointingHandCursor)
            disconnect.setToolTip(
                "Thu hồi token trên Facebook và xóa kết nối Fanpage này khỏi cơ sở dữ liệu"
            )
            disconnect.clicked.connect(
                lambda _=False, p=page_id, n=str(page["page_name"]): self._disconnect(p, n)
            )

            delete_btn = QPushButton("Thu hồi / Xóa DB")
            delete_btn.setObjectName("pageActionDelete")
            delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            delete_btn.setToolTip(
                "Xóa vĩnh viễn Fanpage và Token khỏi cơ sở dữ liệu ứng dụng"
            )
            delete_btn.clicked.connect(
                lambda _=False, p=page_id, n=str(page["page_name"]): self._delete_from_db(
                    p, n
                )
            )

            layout.addWidget(check)
            layout.addWidget(disconnect)
            layout.addWidget(delete_btn)
            layout.addStretch()
            self.table.setCellWidget(row, 8, actions)

    def set_verified(self, *args) -> None:
        pages = args[-1] if args else []
        self.status.setText(f"Đã xác minh {len(pages)} Fanpage từ Facebook.")
        self.refresh_pages()

    def set_status(self, message: str) -> None:
        self.status.setText(message)

    def _connect(self) -> None:
        if not self.token.text().strip():
            self.set_status("Hãy nhập Access Token.")
            return
        self.connectRequested.emit(self.token.text())

    def _test(self) -> None:
        self.testRequested.emit()

    def _sync(self) -> None:
        self.syncRequested.emit()

    def _disconnect(self, page_id: str, page_name: str) -> None:
        if (
            QMessageBox.question(
                self,
                "Ngắt kết nối Fanpage",
                f"Ngắt kết nối '{page_name}'?\n\nHệ thống sẽ thu hồi token trên Facebook (nếu còn hiệu lực), xóa token khỏi Windows Credential Manager và xóa Fanpage khỏi cơ sở dữ liệu.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.disconnectRequested.emit(page_id)

    def _delete_from_db(self, page_id: str, page_name: str) -> None:
        if (
            QMessageBox.question(
                self,
                "Thu hồi & Xóa khỏi Database",
                f"Xóa vĩnh viễn Fanpage '{page_name}' ({page_id}) khỏi cơ sở dữ liệu và Windows Credential Manager?\n\nSau khi xóa, Fanpage sẽ không hiển thị lại khi khởi động lại ứng dụng.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.deleteRequested.emit(page_id)
