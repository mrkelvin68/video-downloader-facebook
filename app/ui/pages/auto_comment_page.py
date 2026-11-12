from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QRadioButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.services.auto_comment_service import (
    AutoCommentService, AutoCommentValidationError, CommentItem, parse_comment_csv, select_comments,
)


class AutoCommentPage(QWidget):
    """Phase 1 planning UI. It intentionally has no Facebook client or worker."""
    def __init__(self, database, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = AutoCommentService(database)
        self.library_id: str | None = None
        self.setAcceptDrops(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 18)
        title = QLabel("Auto Comment")
        title.setObjectName("title")
        root.addWidget(title)
        root.addWidget(QLabel("Lập kế hoạch comment cục bộ. Phase 1 không gửi comment lên Facebook."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSpacing(12)
        layout.addWidget(self._filters())
        layout.addWidget(self._library())
        layout.addWidget(self._selection())
        layout.addWidget(self._preview())
        layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.refresh_pages()
        self.refresh_libraries()

    def _filters(self) -> QGroupBox:
        group = QGroupBox("A — Fanpage & bộ lọc bài đăng")
        form = QFormLayout(group)
        self.page = QComboBox()
        self.page.currentIndexChanged.connect(self._invalidate)
        self.connection_status = QLabel("Chưa chọn Fanpage")
        self.start_date, self.end_date = QDateEdit(), QDateEdit()
        for widget in (self.start_date, self.end_date):
            widget.setCalendarPopup(True); widget.setDisplayFormat("yyyy-MM-dd")
            widget.dateChanged.connect(self._invalidate)
        self.start_date.setDate(date.today().replace(day=1)); self.end_date.setDate(date.today())
        self.content_type = QComboBox(); self.content_type.addItems(["Tất cả", "Reels", "Bài viết thường"])
        self.content_type.currentIndexChanged.connect(self._invalidate)
        form.addRow("Fanpage", self.page); form.addRow("Trạng thái", self.connection_status)
        form.addRow("Từ ngày", self.start_date); form.addRow("Đến ngày", self.end_date); form.addRow("Loại nội dung", self.content_type)
        self.load_posts_button = QPushButton("Tải danh sách bài viết")
        self.load_posts_button.clicked.connect(self.load_posts)
        form.addRow(self.load_posts_button)
        self.post_status = QLabel("Chưa tải bài viết."); self.post_status.setWordWrap(True); form.addRow(self.post_status)
        self.posts: list[dict] = []
        return group

    def _library(self) -> QGroupBox:
        group = QGroupBox("B — Thư viện comment CSV")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.library_name = QLineEdit("Thư viện comment")
        self.library_picker = QComboBox(); self.library_picker.currentIndexChanged.connect(self.open_library)
        for text, handler in (("Import CSV", self.import_csv), ("Thêm dòng", self.add_row), ("Xóa dòng", self.delete_rows), ("Xóa import", self.clear_rows), ("Lưu thư viện", self.save_library)):
            button = QPushButton(text); button.clicked.connect(handler); row.addWidget(button)
        layout.addWidget(self.library_name); layout.addWidget(self.library_picker); layout.addLayout(row)
        self.comments = QTableWidget(0, 3); self.comments.setHorizontalHeaderLabels(["Bật", "ID", "Comment"])
        self.comments.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.comments.horizontalHeader().setStretchLastSection(True)
        self.comments.itemChanged.connect(self._invalidate)
        layout.addWidget(self.comments)
        self.csv_status = QLabel("Kéo-thả tệp .csv vào trang này hoặc dùng Import CSV."); self.csv_status.setWordWrap(True); layout.addWidget(self.csv_status)
        return group

    def _selection(self) -> QGroupBox:
        group = QGroupBox("C — Cấu hình chọn comment")
        layout = QHBoxLayout(group)
        self.one = QRadioButton("Random 1 comment/bài"); self.three = QRadioButton("Random 3 comment/bài"); self.one.setChecked(True)
        self.random_mode = QRadioButton("Ngẫu nhiên"); self.sequential_mode = QRadioButton("Tuần tự"); self.random_mode.setChecked(True)
        self.prevent_duplicates = QCheckBox("Không lặp nội dung trong cùng bài"); self.prevent_duplicates.setChecked(True)
        for widget in (self.one, self.three, self.random_mode, self.sequential_mode, self.prevent_duplicates):
            widget.toggled.connect(self._invalidate); layout.addWidget(widget)
        layout.addStretch()
        return group

    def _preview(self) -> QGroupBox:
        group = QGroupBox("D — Xem trước")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.preview_button = QPushButton("Xem trước comment"); self.preview_button.setObjectName("primary"); self.preview_button.clicked.connect(self.generate_preview)
        self.regenerate_button = QPushButton("Tạo lại preview"); self.regenerate_button.clicked.connect(self.generate_preview)
        row.addWidget(self.preview_button); row.addWidget(self.regenerate_button); row.addStretch(); layout.addLayout(row)
        self.campaign_picker = QComboBox(); self.campaign_picker.currentIndexChanged.connect(self.open_campaign)
        layout.addWidget(self.campaign_picker)
        self.preview = QTableWidget(0, 8)
        self.preview.setHorizontalHeaderLabels(["Chọn", "Fanpage", "Post ID", "Loại", "Published At", "Số comment", "Comments", "Trạng thái"])
        self.preview.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.preview.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.preview)
        self.preview_status = QLabel("Chưa có preview."); self.preview_status.setWordWrap(True); layout.addWidget(self.preview_status)
        disabled = QPushButton("Đăng comment lên Facebook — triển khai ở Phase 2."); disabled.setEnabled(False); layout.addWidget(disabled)
        return group

    def refresh_pages(self) -> None:
        previous = self.page.currentData()
        self.page.blockSignals(True); self.page.clear(); self.page.addItem("Chọn Fanpage", None)
        for item in self.service.list_pages(): self.page.addItem(item["page_name"], item["page_id"])
        index = self.page.findData(previous); self.page.setCurrentIndex(max(0, index)); self.page.blockSignals(False)
        self._page_changed()
        self.page.currentIndexChanged.connect(self._page_changed)

    def _page_changed(self) -> None:
        self.connection_status.setText("Đã có kết nối cục bộ" if self.page.currentData() else "Chưa chọn Fanpage")

    def refresh_libraries(self) -> None:
        self.library_picker.blockSignals(True); self.library_picker.clear(); self.library_picker.addItem("Mở thư viện đã lưu…", None)
        for library in self.service.list_libraries(): self.library_picker.addItem(library["name"], library["id"])
        self.library_picker.blockSignals(False)
        self.campaign_picker.blockSignals(True); self.campaign_picker.clear(); self.campaign_picker.addItem("Mở campaign đã lưu…", None)
        for campaign in self.service.list_campaigns(): self.campaign_picker.addItem(campaign["name"], campaign["id"])
        self.campaign_picker.blockSignals(False)

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn CSV comment", "", "CSV (*.csv)")
        if path: self.load_csv(Path(path))

    def load_csv(self, path: Path) -> None:
        try:
            result = parse_comment_csv(path.read_bytes())
            self.set_items(result.items); self.csv_status.setText("Đã import CSV. " + " ".join(result.warnings))
        except (OSError, AutoCommentValidationError) as exc:
            QMessageBox.warning(self, "CSV comment", str(exc))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(url.toLocalFile().lower().endswith(".csv") for url in event.mimeData().urls()): event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".csv": self.load_csv(path); event.acceptProposedAction(); return

    def set_items(self, items: list[CommentItem]) -> None:
        self.comments.blockSignals(True); self.comments.setRowCount(0)
        for item in items:
            row = self.comments.rowCount(); self.comments.insertRow(row)
            enabled = QTableWidgetItem(); enabled.setCheckState(Qt.CheckState.Checked if item.enabled else Qt.CheckState.Unchecked)
            self.comments.setItem(row, 0, enabled); self.comments.setItem(row, 1, QTableWidgetItem(item.source_row_id or "")); self.comments.setItem(row, 2, QTableWidgetItem(item.comment_text))
        self.comments.blockSignals(False); self._invalidate()

    def items(self) -> list[CommentItem]:
        values = []
        for row in range(self.comments.rowCount()):
            enabled = self.comments.item(row, 0).checkState() == Qt.CheckState.Checked
            source = self.comments.item(row, 1).text().strip() or None; comment = self.comments.item(row, 2).text()
            values.append(CommentItem(comment, enabled, source, row))
        return values

    def add_row(self) -> None: self.set_items(self.items() + [CommentItem("", True, None, self.comments.rowCount())])
    def delete_rows(self) -> None:
        for row in sorted({item.row() for item in self.comments.selectedItems()}, reverse=True): self.comments.removeRow(row)
        self._invalidate()
    def clear_rows(self) -> None: self.comments.setRowCount(0); self.library_id = None; self._invalidate()

    def save_library(self) -> None:
        try:
            self.library_id = self.service.save_library(self.library_name.text(), self.items(), self.library_id); self.refresh_libraries(); self.csv_status.setText("Đã lưu thư viện comment.")
        except AutoCommentValidationError as exc: QMessageBox.warning(self, "Thư viện comment", str(exc))

    def open_library(self) -> None:
        library_id = self.library_picker.currentData()
        if library_id:
            self.library_id = library_id; self.library_name.setText(self.library_picker.currentText()); self.set_items(self.service.load_library(library_id)); self.csv_status.setText("Đã mở thư viện đã lưu.")

    def load_posts(self) -> None:
        page_id = self.page.currentData()
        if not page_id: self.post_status.setText("Hãy chọn Fanpage trước."); return
        if self.start_date.date() > self.end_date.date(): self.post_status.setText("Khoảng ngày không hợp lệ."); return
        content = {"Tất cả": "all", "Reels": "reels", "Bài viết thường": "posts"}[self.content_type.currentText()]
        self.posts = self.service.local_posts(page_id, self.start_date.text(), self.end_date.text(), content)
        self.post_status.setText(f"Đã tải {len(self.posts)} bài có Post ID và thời điểm xuất bản cục bộ. Reels chỉ hiển thị khi dữ liệu cục bộ nhận diện được loại bài.")
        self._invalidate()

    def generate_preview(self) -> None:
        if not self.posts: self.preview_status.setText("Chưa có bài phù hợp trong dữ liệu cục bộ. Hãy tải danh sách bài viết."); return
        try:
            rows = self.items(); count = 3 if self.three.isChecked() else 1; mode = "sequential" if self.sequential_mode.isChecked() else "random"
            comments = {post["post_id"]: select_comments(rows, count, mode) for post in self.posts}
        except AutoCommentValidationError as exc: self.preview_status.setText(str(exc)); return
        self.preview.setRowCount(0)
        page_name = self.page.currentText()
        for post in self.posts:
            row = self.preview.rowCount(); self.preview.insertRow(row)
            selected = QTableWidgetItem(); selected.setCheckState(Qt.CheckState.Checked); self.preview.setItem(row, 0, selected)
            values = [page_name, post["post_id"], post["post_type"], post["published_at"], str(len(comments[post["post_id"]])), "\n".join(comments[post["post_id"]]), "PREVIEW"]
            for column, value in enumerate(values, 1): self.preview.setItem(row, column, QTableWidgetItem(value))
        campaign_id = self.service.save_preview({"name": self.library_name.text() or "Auto Comment draft", "page_id": self.page.currentData(), "start_date": self.start_date.text(), "end_date": self.end_date.text(), "content_type": self.content_type.currentText(), "selection_mode": mode, "count": count, "prevent_duplicates": self.prevent_duplicates.isChecked()}, self.posts, comments)
        self.refresh_libraries()
        self.preview_status.setText(f"Preview đã lưu ({campaign_id[:8]}): {len(self.posts)} bài, {sum(map(len, comments.values()))} comment. Không có comment nào được đăng.")

    def open_campaign(self) -> None:
        campaign_id = self.campaign_picker.currentData()
        if not campaign_id: return
        campaign, rows = self.service.load_campaign_preview(campaign_id)
        grouped: dict[str, list[dict]] = {}
        for item in rows: grouped.setdefault(item["post_id"], []).append(item)
        self.preview.setRowCount(0)
        for post_id, items in grouped.items():
            first = items[0]; row = self.preview.rowCount(); self.preview.insertRow(row)
            selected = QTableWidgetItem(); selected.setCheckState(Qt.CheckState.Checked if first["selected"] else Qt.CheckState.Unchecked); self.preview.setItem(row, 0, selected)
            values = [self.page.itemText(self.page.findData(campaign["page_id"])) or campaign["page_id"], post_id, first["post_type"], first["published_at"], str(len(items)), "\n".join(item["comment_text"] for item in items), "PREVIEW ĐÃ LƯU"]
            for column, value in enumerate(values, 1): self.preview.setItem(row, column, QTableWidgetItem(value))
        self.preview_status.setText(f"Đã mở campaign {campaign['name']}: {len(grouped)} bài, {len(rows)} comment. Không có comment nào được đăng.")

    def _invalidate(self, *_args) -> None:
        if hasattr(self, "preview_status"): self.preview_status.setText("Cấu hình đã thay đổi; preview trước đó không còn hiệu lực.")
