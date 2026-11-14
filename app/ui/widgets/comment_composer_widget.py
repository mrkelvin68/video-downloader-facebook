from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.auto_comment_composer_service import (
    AutoCommentComposerService,
    CommentComposerError,
    ComposedCommentDraft,
    evaluate_draft_warnings,
)


class CommentComposerWidget(QWidget):
    """Local Comment Composer UI (Phase 3C1); never writes composed comments to SQLite."""

    def __init__(
        self,
        database,
        project_id: str | None = None,
        page_id: str | None = None,
        page_name: str = "",
        existing_comments_provider: Callable[[], list[str]] | None = None,
        active_scope_provider: Callable[[], tuple[str, str] | None] | None = None,
        saved_callback: Callable[[], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.service = AutoCommentComposerService(database)
        self._captured_scope = (project_id, page_id)
        self.project_id = project_id
        self.page_id = page_id
        self.page_name = page_name
        self._existing_comments_provider = existing_comments_provider
        self._active_scope_provider = active_scope_provider
        self._saved_callback = saved_callback
        self._scope_invalidated = False
        self._templates_by_id: dict[str, dict] = {}
        self._links_by_id: dict[str, dict] = {}
        self.current_draft: ComposedCommentDraft | None = None
        self._draft_saved = False

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.page_display = QLabel()
        self.page_display.setWordWrap(True)

        self.template_combo = QComboBox()
        self.template_combo.currentIndexChanged.connect(self._on_template_selected)

        self.link_combo = QComboBox()
        self.link_combo.currentIndexChanged.connect(self._on_link_selected)

        self.template_preview = QPlainTextEdit()
        self.template_preview.setReadOnly(True)
        self.template_preview.setMaximumHeight(95)
        self.template_preview.setPlaceholderText("Nội dung mẫu comment sẽ hiển thị tại đây…")

        self.link_preview = QLineEdit()
        self.link_preview.setReadOnly(True)
        self.link_preview.setPlaceholderText("Affiliate URL nguyên bản sẽ hiển thị tại đây…")

        form.addRow("Fanpage đang chọn", self.page_display)
        form.addRow("Mẫu comment (đang bật)", self.template_combo)
        form.addRow("Xem trước mẫu", self.template_preview)
        form.addRow("Affiliate URL (đang bật)", self.link_combo)
        form.addRow("Xem trước URL", self.link_preview)
        layout.addLayout(form)

        action_bar = QHBoxLayout()
        self.compose_button = QPushButton("Tạo bản nháp")
        self.compose_button.clicked.connect(self.compose_draft)
        self.refresh_button = QPushButton("Làm mới danh sách")
        self.refresh_button.clicked.connect(self.reload_options)
        action_bar.addWidget(self.compose_button)
        action_bar.addWidget(self.refresh_button)
        action_bar.addStretch()
        layout.addLayout(action_bar)

        self.source_info_label = QLabel("Chưa tạo bản nháp.")
        self.source_info_label.setWordWrap(True)
        layout.addWidget(self.source_info_label)

        self.composed_preview = QPlainTextEdit()
        self.composed_preview.setPlaceholderText(
            "Nhấn 'Tạo bản nháp' để ghép mẫu và Affiliate URL. Bạn có thể chỉnh sửa nội dung cục bộ tại đây…"
        )
        self.composed_preview.textChanged.connect(self._on_composed_text_edited)
        layout.addWidget(self.composed_preview, 1)

        self.library_combo = QComboBox()
        self.library_combo.currentIndexChanged.connect(self._update_save_enabled)
        form.addRow("Thư viện đích", self.library_combo)

        self.approval_checkbox = QCheckBox("Duyệt comment này")
        self.approval_checkbox.setChecked(False)
        self.approval_checkbox.toggled.connect(self._update_save_enabled)
        layout.addWidget(self.approval_checkbox)

        self.save_button = QPushButton("Lưu vào thư viện comment")
        self.save_button.clicked.connect(self.save_approved_draft)
        layout.addWidget(self.save_button)

        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.reload_options()

    def _reset_approval(self):
        self.approval_checkbox.blockSignals(True)
        self.approval_checkbox.setChecked(False)
        self.approval_checkbox.blockSignals(False)
        self._draft_saved = False
        self._update_save_enabled()

    def _update_save_enabled(self, *_):
        self.save_button.setEnabled(bool(
            self.current_draft and self._has_valid_scope() and not self._draft_saved
            and self.approval_checkbox.isChecked() and self.library_combo.currentData()
        ))

    def _reload_destination_libraries(self):
        selected = self.library_combo.currentData()
        self.library_combo.blockSignals(True)
        self.library_combo.clear()
        self.library_combo.addItem("— Chọn thư viện comment đích —", None)
        if self._has_valid_scope():
            for library in self.service.list_destination_libraries(self.project_id, self.page_id):
                self.library_combo.addItem(library["name"], library["id"])
        self.library_combo.setCurrentIndex(max(0, self.library_combo.findData(selected)))
        self.library_combo.blockSignals(False)
        self._update_save_enabled()

    def _has_valid_scope(self) -> bool:
        return (
            not self._scope_invalidated
            and bool(str(self.project_id or "").strip())
            and bool(str(self.page_id or "").strip())
            and (self.project_id, self.page_id) == self._captured_scope
        )

    def _update_page_header(self):
        if self._scope_invalidated:
            self.page_display.setText(
                f"{self.page_name or self.page_id or 'Chưa chọn'} (Fanpage đã thay đổi — cần mở lại)"
            )
        elif self.project_id and self.page_id:
            label = f"{self.page_name} · {self.page_id}" if self.page_name else str(self.page_id)
            self.page_display.setText(label)
        else:
            self.page_display.setText("Chưa chọn Fanpage")

    def on_context_changed(
        self,
        new_project_id: str | None,
        new_page_id: str | None,
        new_page_name: str = "",
    ):
        """Invalidate stale selections when the active Page changes without silently switching scope."""
        if (new_project_id, new_page_id) != self._captured_scope:
            self._scope_invalidated = True
            self.current_draft = None
            self._templates_by_id.clear()
            self._links_by_id.clear()
            self.template_combo.blockSignals(True)
            self.link_combo.blockSignals(True)
            self.template_combo.clear()
            self.template_combo.addItem("Fanpage đã thay đổi — vui lòng đóng hộp thoại", None)
            self.link_combo.clear()
            self.link_combo.addItem("Fanpage đã thay đổi — vui lòng đóng hộp thoại", None)
            self.template_combo.blockSignals(False)
            self.link_combo.blockSignals(False)
            self.template_combo.setEnabled(False)
            self.link_combo.setEnabled(False)
            self.compose_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.library_combo.setEnabled(False)
            self.template_preview.setPlainText("")
            self.link_preview.setText("")
            self.composed_preview.blockSignals(True)
            self.composed_preview.setPlainText("")
            self.composed_preview.blockSignals(False)
            self.source_info_label.setText("Đã vô hiệu hóa lựa chọn cũ do đổi Fanpage.")
            self.warning_label.setText("")
            self.status_label.setText(
                "Fanpage đang chọn đã thay đổi. Vui lòng đóng hộp thoại và mở lại 'Tạo comment từ mẫu' cho Fanpage mới."
            )
            self._update_page_header()

    def reload_options(self):
        self._update_page_header()
        if not self._has_valid_scope():
            self.template_combo.setEnabled(False)
            self.link_combo.setEnabled(False)
            self.compose_button.setEnabled(False)
            if not self._scope_invalidated:
                self.status_label.setText(
                    "Hãy chọn Fanpage trước khi tạo comment từ mẫu."
                )
            return

        prev_template_id = self.selected_template_id()
        prev_link_id = self.selected_link_id()

        templates = self.service.list_enabled_templates(self.project_id, self.page_id)
        links = self.service.list_enabled_links(self.project_id, self.page_id)
        self._templates_by_id = {str(t["id"]): t for t in templates}
        self._links_by_id = {str(l["id"]): l for l in links}

        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem("— Chọn mẫu comment —", None)
        restored_t_idx = 0
        for idx, t in enumerate(templates, start=1):
            tid = str(t["id"])
            tag = " [có {affiliate_url}]" if "{affiliate_url}" in str(t.get("body_text") or "") else ""
            self.template_combo.addItem(f"{t['name']}{tag}", tid)
            if prev_template_id and tid == prev_template_id:
                restored_t_idx = idx
        self.template_combo.setCurrentIndex(restored_t_idx)
        self.template_combo.blockSignals(False)

        self.link_combo.blockSignals(True)
        self.link_combo.clear()
        self.link_combo.addItem("— Không chọn / Chọn Affiliate URL —", None)
        restored_l_idx = 0
        for idx, link in enumerate(links, start=1):
            lid = str(link["id"])
            self.link_combo.addItem(f"{link['name']} ({link['url']})", lid)
            if prev_link_id and lid == prev_link_id:
                restored_l_idx = idx
        self.link_combo.setCurrentIndex(restored_l_idx)
        self.link_combo.blockSignals(False)

        self.template_combo.setEnabled(True)
        self.link_combo.setEnabled(True)
        self.compose_button.setEnabled(True)
        self.library_combo.setEnabled(True)
        self._reload_destination_libraries()
        self._on_template_selected()
        self._on_link_selected()

        if not templates and not links:
            self.status_label.setText(
                "Fanpage này chưa có mẫu comment hoặc Affiliate URL đang bật."
            )
        elif not templates:
            self.status_label.setText(
                "Fanpage này chưa có mẫu comment đang bật. Hãy thêm hoặc bật mẫu trong 'Kho mẫu comment'."
            )
        else:
            self.status_label.setText(
                f"Đã tải {len(templates)} mẫu đang bật và {len(links)} Affiliate URL đang bật."
            )

    def selected_template_id(self) -> str | None:
        data = self.template_combo.currentData()
        return str(data) if data else None

    def selected_link_id(self) -> str | None:
        data = self.link_combo.currentData()
        return str(data) if data else None

    def _on_template_selected(self, *_):
        if self.current_draft is not None:
            self.current_draft = None
            self._reset_approval()
        tid = self.selected_template_id()
        tpl = self._templates_by_id.get(tid) if tid else None
        self.template_preview.setPlainText(str(tpl.get("body_text") or "") if tpl else "")

    def _on_link_selected(self, *_):
        if self.current_draft is not None:
            self.current_draft = None
            self._reset_approval()
        lid = self.selected_link_id()
        link = self._links_by_id.get(lid) if lid else None
        url = str(link.get("url") or "") if link else ""
        self.link_preview.setText(url)
        self.link_preview.setToolTip(url)

    def _existing_comments(self) -> list[str]:
        if self._existing_comments_provider is None:
            return []
        try:
            return list(self._existing_comments_provider() or [])
        except Exception:
            return []

    def compose_draft(self) -> ComposedCommentDraft | None:
        if not self._has_valid_scope():
            self.current_draft = None
            self.status_label.setText(
                "Fanpage đang chọn đã thay đổi hoặc không hợp lệ; không thể tạo bản nháp bằng dữ liệu cũ."
            )
            return None

        tid = self.selected_template_id()
        lid = self.selected_link_id()
        try:
            draft = self.service.compose_draft(
                self.project_id,
                self.page_id,
                tid or "",
                lid,
                existing_comments=self._existing_comments(),
            )
        except CommentComposerError as exc:
            self.current_draft = None
            self.warning_label.setText("")
            self.status_label.setText(str(exc))
            return None

        self.current_draft = draft
        self._reset_approval()
        self.composed_preview.blockSignals(True)
        self.composed_preview.setPlainText(draft.composed_text)
        self.composed_preview.blockSignals(False)

        link_info = (
            f"{draft.affiliate_link_name} ({draft.affiliate_link_id})"
            if draft.affiliate_link_id and draft.has_placeholder
            else "Không gắn link"
        )
        self.source_info_label.setText(
            f"Nguồn mẫu: {draft.template_name} ({draft.template_id}) · Nguồn link: {link_info}"
        )
        self.warning_label.setText(" | ".join(draft.warnings) if draft.warnings else "")
        self.status_label.setText(
            "Đã tạo bản nháp cục bộ. Bạn có thể chỉnh sửa nội dung; chưa lưu vào thư viện hay chiến dịch."
        )
        return draft

    def _on_composed_text_edited(self):
        if self.current_draft is None or not self._has_valid_scope():
            return
        edited = self.composed_preview.toPlainText()
        self.current_draft.edited_text = edited
        self._reset_approval()
        try:
            warnings = evaluate_draft_warnings(edited, self._existing_comments())
            if not self.current_draft.has_placeholder and self.current_draft.affiliate_link_id:
                notice = "Mẫu không có placeholder {affiliate_url}; hệ thống giữ nguyên văn bản mẫu và không tự động chèn link."
                if notice not in warnings:
                    warnings.append(notice)
            self.current_draft.warnings = warnings
            self.warning_label.setText(" | ".join(warnings) if warnings else "")
            self.status_label.setText("Bản nháp đã được chỉnh sửa cục bộ (chưa lưu SQLite).")
        except CommentComposerError as exc:
            self.warning_label.setText(str(exc))

    def save_approved_draft(self):
        if self._draft_saved:
            self.status_label.setText("Bản nháp này đã được lưu; hãy tạo bản nháp mới để lưu thêm.")
            return None
        if not self.current_draft or not self._has_valid_scope():
            self.status_label.setText("Bản nháp không còn hợp lệ; hãy tạo lại trước khi lưu.")
            return None
        active_scope = self._active_scope_provider() if self._active_scope_provider else self._captured_scope
        if active_scope != self._captured_scope:
            self.on_context_changed(*(active_scope or (None, None)))
            return None
        if not self.approval_checkbox.isChecked():
            self.status_label.setText("Hãy tích 'Duyệt comment này' trước khi lưu.")
            return None
        library_id = self.library_combo.currentData()
        if not library_id:
            self.status_label.setText("Hãy chọn thư viện comment đích trước khi lưu.")
            return None
        try:
            item_id = self.service.save_approved_draft(
                self.current_draft, str(library_id), self.project_id, self.page_id
            )
        except CommentComposerError as exc:
            self.status_label.setText(str(exc))
            return None
        self._draft_saved = True
        self._update_save_enabled()
        self.status_label.setText("Đã lưu chính xác một comment đã duyệt vào thư viện.")
        if self._saved_callback:
            self._saved_callback()
        return item_id


class CommentComposerDialog(QDialog):
    def __init__(
        self,
        database,
        project_id: str,
        page_id: str,
        page_name: str = "",
        existing_comments_provider: Callable[[], list[str]] | None = None,
        active_scope_provider: Callable[[], tuple[str, str] | None] | None = None,
        saved_callback: Callable[[], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Tạo comment từ mẫu")
        self.setMinimumSize(820, 560)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)

        layout = QVBoxLayout(self)
        self.composer_widget = CommentComposerWidget(
            database,
            project_id=project_id,
            page_id=page_id,
            page_name=page_name,
            existing_comments_provider=existing_comments_provider,
            active_scope_provider=active_scope_provider,
            saved_callback=saved_callback,
            parent=self,
        )
        layout.addWidget(self.composer_widget, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

    def on_context_changed(
        self,
        new_project_id: str | None,
        new_page_id: str | None,
        new_page_name: str = "",
    ):
        self.composer_widget.on_context_changed(
            new_project_id, new_page_id, new_page_name
        )
