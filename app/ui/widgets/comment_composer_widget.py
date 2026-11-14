from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QPushButton

from app.services.auto_comment_composer_service import AutoCommentComposerService, CommentComposerError, ComposedCommentDraft, evaluate_draft_warnings


class CommentComposerWidget(QWidget):
    """Local bulk mixer; it never posts to Facebook or creates a campaign."""
    def __init__(self, database, project_id=None, page_id=None, page_name="", existing_comments_provider: Callable[[], list[str]] | None = None, active_scope_provider=None, saved_callback=None, parent=None):
        super().__init__(parent)
        self.service = AutoCommentComposerService(database); self.project_id = project_id; self.page_id = page_id; self._scope = (project_id, page_id)
        self._existing = existing_comments_provider or (lambda: []); self._active_scope = active_scope_provider or (lambda: self._scope); self._saved_callback = saved_callback; self._saved = False; self.drafts: list[ComposedCommentDraft] = []
        root = QVBoxLayout(self); root.addWidget(QLabel(f"Fanpage: {page_name or page_id or 'Chưa chọn'}"))
        sources = QHBoxLayout(); self.template_list, self.template_toggle, self.template_count = self._source_box("Kho comment / mẫu", sources); self.link_list, self.link_toggle, self.link_count = self._source_box("Kho link AFF", sources); root.addLayout(sources)
        form = QFormLayout(); modes = QHBoxLayout(); self.round_robin = QRadioButton("Lần lượt — xoay vòng link"); self.combinations = QRadioButton("Tất cả kết hợp"); self.round_robin.setChecked(True); modes.addWidget(self.round_robin); modes.addWidget(self.combinations)
        placements = QHBoxLayout(); self.end_placement = QRadioButton("Cuối comment"); self.placeholder_placement = QRadioButton("Dùng {affiliate_url}"); self.end_placement.setChecked(True); placements.addWidget(self.end_placement); placements.addWidget(self.placeholder_placement)
        self.limit = QSpinBox(); self.limit.setRange(1, 500); self.limit.setValue(10); self.potential = QLabel()
        form.addRow("Cách trộn", modes); form.addRow("Vị trí link", placements); form.addRow("Giới hạn bản nháp", self.limit); form.addRow("Dự kiến", self.potential); root.addLayout(form)
        self.mix_button = QPushButton("Trộn comment"); self.mix_button.clicked.connect(self.mix); root.addWidget(self.mix_button)
        self.results = QTableWidget(0, 5); self.results.setHorizontalHeaderLabels(["Chọn", "Nội dung sau trộn", "Mẫu", "Link", "Cảnh báo"]); self.results.setColumnWidth(0, 55); self.results.setColumnWidth(2, 150); self.results.setColumnWidth(3, 150); self.results.horizontalHeader().setStretchLastSection(True); self.results.itemChanged.connect(self._result_changed); root.addWidget(self.results, 1)
        actions = QHBoxLayout(); self.select_results = QPushButton("Chọn tất cả"); self.select_results.clicked.connect(self.toggle_results); self.destination = QComboBox(); self.destination.currentIndexChanged.connect(self._update_save); self.save_button = QPushButton("Lưu comment đã chọn"); self.save_button.clicked.connect(self.save_selected); actions.addWidget(self.select_results); actions.addWidget(QLabel("Thư viện đích")); actions.addWidget(self.destination, 1); actions.addWidget(self.save_button); root.addLayout(actions)
        self.status_label = QLabel(); self.status_label.setWordWrap(True); root.addWidget(self.status_label)
        for control in (self.round_robin, self.combinations, self.end_placement, self.placeholder_placement, self.limit): (control.toggled if hasattr(control, "toggled") else control.valueChanged).connect(self._update_potential)
        self.reload_options()

    def _source_box(self, title, parent):
        column = QVBoxLayout(); column.addWidget(QLabel(title)); bar = QHBoxLayout(); toggle = QPushButton("Chọn tất cả"); count = QLabel(); bar.addWidget(toggle); bar.addWidget(count); bar.addStretch(); column.addLayout(bar); listing = QListWidget(); listing.itemChanged.connect(self._update_potential); toggle.clicked.connect(lambda: self._toggle_sources(listing, toggle)); column.addWidget(listing, 1); parent.addLayout(column, 1); return listing, toggle, count

    @staticmethod
    def _ids(listing): return [str(listing.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(listing.count()) if listing.item(i).checkState() == Qt.CheckState.Checked]
    def _toggle_sources(self, listing, button):
        items = [listing.item(i) for i in range(listing.count())]; checked = bool(items) and all(item.checkState() == Qt.CheckState.Checked for item in items)
        for item in items: item.setCheckState(Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)
        button.setText("Chọn tất cả" if checked else "Bỏ chọn tất cả")
    def reload_options(self):
        self.template_list.clear(); self.link_list.clear(); self.destination.clear(); self.destination.addItem("— Chọn thư viện đích —", None)
        if not self.project_id or not self.page_id: self.mix_button.setEnabled(False); return
        for listing, rows in ((self.template_list, self.service.list_enabled_templates(self.project_id, self.page_id)), (self.link_list, self.service.list_enabled_links(self.project_id, self.page_id))):
            for row in rows:
                item = QListWidgetItem(str(row["name"])); item.setData(Qt.ItemDataRole.UserRole, row["id"]); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Unchecked); listing.addItem(item)
        for library in self.service.list_destination_libraries(self.project_id, self.page_id): self.destination.addItem(library["name"], library["id"])
        self._update_potential()
    def _update_potential(self, *_):
        templates, links = self._ids(self.template_list), self._ids(self.link_list); total = len(templates) * (len(links) if self.combinations.isChecked() else 1)
        self.template_count.setText(f"{len(templates)} đã chọn"); self.link_count.setText(f"{len(links)} đã chọn"); self.potential.setText(f"{min(total, self.limit.value())} / {total} bản nháp"); self._update_save()
    def mix(self):
        if self._active_scope() != self._scope: self.status_label.setText("Fanpage đã thay đổi; hãy mở lại hộp thoại."); return
        try: self.drafts = self.service.mix_drafts(self.project_id, self.page_id, self._ids(self.template_list), self._ids(self.link_list), "all" if self.combinations.isChecked() else "round_robin", "placeholder" if self.placeholder_placement.isChecked() else "end", self.limit.value(), list(self._existing() or []))
        except CommentComposerError as exc: self.status_label.setText(str(exc)); return
        self._saved = False; self.results.blockSignals(True); self.results.setRowCount(0)
        for draft in self.drafts:
            row = self.results.rowCount(); self.results.insertRow(row); check = QTableWidgetItem(); check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable); check.setCheckState(Qt.CheckState.Checked); self.results.setItem(row, 0, check); self.results.setItem(row, 1, QTableWidgetItem(draft.edited_text)); self.results.setItem(row, 2, QTableWidgetItem(draft.template_name)); self.results.setItem(row, 3, QTableWidgetItem(draft.affiliate_link_name or "")); self.results.setItem(row, 4, QTableWidgetItem(" | ".join(draft.warnings)))
        self.results.blockSignals(False); self.status_label.setText(f"Đã tạo {len(self.drafts)} bản nháp cục bộ; chưa lưu."); self._update_save()
    def _result_changed(self, item):
        if item.column() == 1 and item.row() < len(self.drafts):
            draft = self.drafts[item.row()]; draft.edited_text = item.text()
            try: draft.warnings = evaluate_draft_warnings(draft.edited_text, list(self._existing() or []))
            except CommentComposerError as exc: draft.warnings = [str(exc)]
            self.results.item(item.row(), 4).setText(" | ".join(draft.warnings))
        self._update_save()
    def toggle_results(self):
        checked = self.results.rowCount() and all(self.results.item(r, 0).checkState() == Qt.CheckState.Checked for r in range(self.results.rowCount()))
        for row in range(self.results.rowCount()): self.results.item(row, 0).setCheckState(Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)
        self.select_results.setText("Chọn tất cả" if checked else "Bỏ chọn tất cả")
    def _update_save(self, *_):
        selected = any(self.results.item(r, 0) and self.results.item(r, 0).checkState() == Qt.CheckState.Checked for r in range(self.results.rowCount())); self.save_button.setEnabled(bool(selected and self.destination.currentData() and not self._saved and self._active_scope() == self._scope))
    def save_selected(self):
        if self._saved or self._active_scope() != self._scope: self.status_label.setText("Bản nháp không còn hợp lệ hoặc đã được lưu."); return None
        chosen = [draft for row, draft in enumerate(self.drafts) if self.results.item(row, 0).checkState() == Qt.CheckState.Checked]
        try: ids = self.service.save_selected_drafts(chosen, str(self.destination.currentData() or ""), self.project_id, self.page_id)
        except CommentComposerError as exc: self.status_label.setText(str(exc)); return None
        self._saved = True; self.status_label.setText(f"Đã lưu {len(ids)} comment vào thư viện."); self._update_save()
        if self._saved_callback: self._saved_callback()
        return ids


class CommentComposerDialog(QDialog):
    def __init__(self, database, project_id, page_id, page_name="", existing_comments_provider=None, active_scope_provider=None, saved_callback=None, parent=None):
        super().__init__(parent); self.setWindowTitle("Trộn Comment + Link"); self.setMinimumSize(980, 700); self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint); layout = QVBoxLayout(self); self.composer_widget = CommentComposerWidget(database, project_id, page_id, page_name, existing_comments_provider, active_scope_provider, saved_callback, self); layout.addWidget(self.composer_widget, 1); close = QPushButton("Đóng"); close.clicked.connect(self.reject); layout.addWidget(close)
    def on_context_changed(self, *_): self.composer_widget.save_button.setEnabled(False); self.composer_widget.status_label.setText("Fanpage đã thay đổi; không thể lưu bản nháp cũ.")
