from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.auto_comment_affiliate_link_service import (
    AffiliateLinkError,
    AutoCommentAffiliateLinkService,
)

_TABLE_STYLE = (
    "QTableWidget{selection-background-color:#1d4ed8;selection-color:white;outline:none}"
    "QTableWidget::item{outline:none}"
    "QTableWidget::item:focus{outline:none;border:none}"
    "QTableWidget::indicator{width:18px;height:18px;border:2px solid #64748b;border-radius:3px;background:#0f172a}"
    "QTableWidget::indicator:hover{border-color:#94a3b8}"
    "QTableWidget::indicator:checked{background:#2563eb;border-color:#60a5fa}"
)


def _display_url(url: str, max_length: int = 80) -> str:
    text = str(url or "")
    if len(text) <= max_length:
        return text
    return f"{text[:max_length - 1]}…"


class _AffiliateLinkTable(QTableWidget):
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and self.currentColumn() == 0:
            item = self.currentItem()
            if (
                item is not None
                and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
                and (item.flags() & Qt.ItemFlag.ItemIsEnabled)
            ):
                item.setCheckState(
                    Qt.CheckState.Unchecked
                    if item.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                event.accept()
                return
        super().keyPressEvent(event)


class CommentAffiliateLinkLibraryWidget(QWidget):
    def __init__(self, database, project_id=None, page_id=None, parent=None):
        super().__init__(parent)
        self.service = AutoCommentAffiliateLinkService(database)
        self.project_id = project_id
        self.page_id = page_id
        self.editor_dialog = None
        self.setStyleSheet(_TABLE_STYLE)

        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Tìm theo tên sản phẩm, mô tả hoặc URL…")
        self.search.textChanged.connect(self.reload)
        self.add_button = QPushButton("Thêm link")
        self.add_button.clicked.connect(lambda: self.create_link_dialog())
        self.edit_button = QPushButton("Sửa link")
        self.edit_button.clicked.connect(lambda: self.edit_selected_link())
        self.delete_button = QPushButton("Xóa link")
        self.delete_button.clicked.connect(lambda: self.delete())
        self.copy_button = QPushButton("Sao chép URL")
        self.copy_button.clicked.connect(lambda: self.copy_selected_url())
        self.refresh_button = QPushButton("Làm mới")
        self.refresh_button.clicked.connect(lambda: self.reload())
        for widget in (
            self.add_button,
            self.edit_button,
            self.delete_button,
            self.copy_button,
            self.refresh_button,
            self.search,
        ):
            bar.addWidget(widget)
        layout.addLayout(bar)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.table = _AffiliateLinkTable(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Bật", "Tên sản phẩm", "Affiliate URL", "Mô tả", "Cập nhật"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 58)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(2, 320)
        self.table.setColumnWidth(3, 180)
        self.table.itemChanged.connect(self.toggle)
        layout.addWidget(self.table, 1)

        self.reload()

    def _has_active_scope(self) -> bool:
        return bool(str(self.project_id or "").strip() and str(self.page_id or "").strip())

    def set_context(self, project_id, page_id):
        old_scope = (self.project_id, self.page_id)
        self.project_id = project_id
        self.page_id = page_id
        self.reload()
        if (project_id, page_id) != old_scope:
            if not self._has_active_scope():
                self.status_label.setText(
                    "Hãy chọn Fanpage trước khi quản lý kho link Affiliate."
                )
            else:
                self.status_label.setText(
                    "Đã chuyển Fanpage; danh sách link Affiliate đã được cập nhật theo Fanpage mới."
                )
            if self.editor_dialog is not None and hasattr(self.editor_dialog, "on_context_changed"):
                self.editor_dialog.on_context_changed()

    def reload(self, *_):
        prev_id = self.selected()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        restored_row = -1
        has_scope = self._has_active_scope()
        for btn in (self.add_button, self.edit_button, self.delete_button, self.copy_button):
            btn.setEnabled(has_scope)

        if has_scope:
            try:
                items = self.service.list_links(
                    self.project_id,
                    self.page_id,
                    search=self.search.text(),
                )
            except AffiliateLinkError as exc:
                items = []
                self.status_label.setText(str(exc))
            for item in items:
                r = self.table.rowCount()
                self.table.insertRow(r)
                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsSelectable
                )
                check.setCheckState(
                    Qt.CheckState.Checked if item["enabled"] else Qt.CheckState.Unchecked
                )
                check.setData(Qt.ItemDataRole.UserRole, item["id"])
                self.table.setItem(r, 0, check)

                name_item = QTableWidgetItem(item["name"] or "")
                name_item.setToolTip(item["name"] or "")
                self.table.setItem(r, 1, name_item)

                raw_url = item["url"] or ""
                url_item = QTableWidgetItem(_display_url(raw_url))
                url_item.setData(Qt.ItemDataRole.UserRole, raw_url)
                url_item.setToolTip(raw_url)
                self.table.setItem(r, 2, url_item)

                desc_text = item.get("description") or ""
                desc_item = QTableWidgetItem(desc_text)
                desc_item.setToolTip(desc_text)
                self.table.setItem(r, 3, desc_item)

                updated_item = QTableWidgetItem(item["updated_at"] or "")
                updated_item.setToolTip(item["updated_at"] or "")
                self.table.setItem(r, 4, updated_item)

                if prev_id and item["id"] == prev_id:
                    restored_row = r
            if not self.status_label.text() or "Hãy chọn Fanpage" in self.status_label.text():
                self.status_label.setText(f"Đang hiển thị {self.table.rowCount()} link Affiliate.")
        else:
            self.status_label.setText("Hãy chọn Fanpage trước khi quản lý kho link Affiliate.")

        self.table.blockSignals(False)
        if restored_row >= 0:
            self.table.setCurrentCell(restored_row, 0)
        elif self.table.rowCount() > 0:
            self.table.setCurrentCell(0, 0)

    def selected(self):
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row, 0) is not None:
            return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        return None

    def selected_url(self) -> str | None:
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row, 2) is not None:
            raw = self.table.item(row, 2).data(Qt.ItemDataRole.UserRole)
            return str(raw) if raw is not None else self.table.item(row, 2).text()
        return None

    def copy_selected_url(self) -> str | None:
        if not self._has_active_scope():
            QMessageBox.information(
                self,
                "Kho link AFF",
                "Hãy chọn Fanpage trước khi sao chép link Affiliate.",
            )
            return None
        url = self.selected_url()
        if not url:
            self.status_label.setText("Hãy chọn một dòng để sao chép URL.")
            return None
        QApplication.clipboard().setText(url)
        self.status_label.setText("Đã sao chép Affiliate URL vào clipboard.")
        return url

    def toggle(self, item):
        if item.column() == 0:
            if not self._has_active_scope():
                self.reload()
                return
            try:
                self.service.set_link_enabled(
                    self.project_id,
                    self.page_id,
                    item.data(Qt.ItemDataRole.UserRole),
                    item.checkState() == Qt.CheckState.Checked,
                )
            except AffiliateLinkError as exc:
                QMessageBox.warning(self, "Kho link AFF", str(exc))
                self.reload()

    def create_link_dialog(self):
        return self._editor(None)

    def edit_selected_link(self):
        link_id = self.selected()
        if link_id:
            return self._editor(link_id)
        return None

    def create_editor_dialog(self, link_id=None):
        if not self._has_active_scope():
            QMessageBox.information(
                self,
                "Kho link AFF",
                "Hãy chọn Fanpage trước khi thêm hoặc sửa link Affiliate.",
            )
            return None
        captured_scope = (self.project_id, self.page_id)
        captured_project_id, captured_page_id = captured_scope
        try:
            old = (
                self.service.get_link(captured_project_id, captured_page_id, link_id)
                if link_id
                else {}
            )
        except AffiliateLinkError as exc:
            QMessageBox.warning(self, "Kho link AFF", str(exc))
            return None

        d = QDialog(self)
        d.setWindowTitle("Link sản phẩm Affiliate")
        d.setMinimumWidth(520)
        form = QFormLayout(d)

        name_edit = QLineEdit(old.get("name", ""))
        url_edit = QLineEdit(old.get("url", ""))
        url_edit.setPlaceholderText("https://s.shopee.vn/...")
        desc_edit = QPlainTextEdit(old.get("description", "") or "")
        desc_edit.setMaximumHeight(90)
        enabled_check = QCheckBox("Bật/Tắt")
        enabled_check.setChecked(bool(old.get("enabled", True)))
        context_warning = QLabel()
        context_warning.setWordWrap(True)
        context_warning.hide()

        form.addRow("Tên sản phẩm/link", name_edit)
        form.addRow("Affiliate URL", url_edit)
        form.addRow("Mô tả sản phẩm", desc_edit)
        form.addRow(enabled_check)
        form.addRow(context_warning)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_button = QPushButton("Lưu")
        cancel_button = QPushButton("Hủy")
        btn_row.addWidget(save_button)
        btn_row.addWidget(cancel_button)
        form.addRow(btn_row)

        def on_context_changed():
            if (self.project_id, self.page_id) != captured_scope:
                save_button.setEnabled(False)
                context_warning.setText(
                    "Fanpage đang chọn đã thay đổi. Vui lòng đóng hộp thoại và mở lại cho Fanpage mới."
                )
                context_warning.show()

        def save():
            if (self.project_id, self.page_id) != captured_scope:
                QMessageBox.warning(
                    d,
                    "Kho link AFF",
                    "Fanpage đang chọn đã thay đổi. Không thể lưu link vào Fanpage khác.",
                )
                return
            try:
                if link_id:
                    self.service.update_link(
                        captured_project_id,
                        captured_page_id,
                        link_id,
                        name_edit.text(),
                        url_edit.text(),
                        desc_edit.toPlainText(),
                        enabled_check.isChecked(),
                    )
                else:
                    self.service.create_link(
                        captured_project_id,
                        captured_page_id,
                        name_edit.text(),
                        url_edit.text(),
                        desc_edit.toPlainText(),
                        enabled_check.isChecked(),
                    )
                d.accept()
                self.reload()
                self.status_label.setText("Đã lưu link Affiliate.")
            except AffiliateLinkError as exc:
                QMessageBox.warning(d, "Kho link AFF", str(exc))
            except Exception as exc:
                QMessageBox.warning(
                    d,
                    "Kho link AFF",
                    f"Không thể lưu link sản phẩm do lỗi cơ sở dữ liệu: {exc}",
                )

        save_button.clicked.connect(save)
        cancel_button.clicked.connect(d.reject)

        d.name_edit = name_edit
        d.url_edit = url_edit
        d.description_edit = desc_edit
        d.enabled_checkbox = enabled_check
        d.save_button = save_button
        d.cancel_button = cancel_button
        d.context_warning = context_warning
        d.save_link = save
        d.on_context_changed = on_context_changed
        self.editor_dialog = d
        return d

    def _editor(self, link_id):
        d = self.create_editor_dialog(link_id)
        if d is not None:
            d.exec()
        return d

    def delete(self):
        if not self._has_active_scope():
            QMessageBox.information(
                self,
                "Kho link AFF",
                "Hãy chọn Fanpage trước khi xóa link Affiliate.",
            )
            return
        link_id = self.selected()
        if not link_id:
            return
        captured_scope = (self.project_id, self.page_id)
        if (
            QMessageBox.question(
                self,
                "Xóa link Affiliate",
                "Xóa link sản phẩm đã chọn?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            if (self.project_id, self.page_id) != captured_scope:
                QMessageBox.warning(
                    self,
                    "Kho link AFF",
                    "Fanpage đang chọn đã thay đổi. Đã hủy thao tác xóa.",
                )
                return
            try:
                self.service.delete_link(self.project_id, self.page_id, link_id)
                self.reload()
                self.status_label.setText("Đã xóa link Affiliate.")
            except AffiliateLinkError as exc:
                QMessageBox.warning(self, "Kho link AFF", str(exc))
                self.reload()
