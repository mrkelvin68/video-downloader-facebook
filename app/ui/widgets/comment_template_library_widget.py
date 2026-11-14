from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
from app.services.ai_comment_service import generate_text_templates
from app.services.auto_comment_template_service import AutoCommentTemplateService, TemplateError, _validate
from app.ui.facebook_worker import FacebookRequestWorker
from app.ui.widgets.loading_button import LoadingButton

_TABLE_STYLE = (
    "QTableWidget{selection-background-color:#1d4ed8;selection-color:white;outline:none}"
    "QTableWidget::item{outline:none}"
    "QTableWidget::item:focus{outline:none;border:none}"
    "QTableWidget::indicator{width:18px;height:18px;border:2px solid #64748b;border-radius:3px;background:#0f172a}"
    "QTableWidget::indicator:hover{border-color:#94a3b8}"
    "QTableWidget::indicator:checked{background:#2563eb;border-color:#60a5fa}"
)


class _TemplateTable(QTableWidget):
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and self.currentColumn() == 0:
            item = self.currentItem()
            if item is not None and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable) and (item.flags() & Qt.ItemFlag.ItemIsEnabled):
                item.setCheckState(
                    Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
                )
                event.accept()
                return
        super().keyPressEvent(event)


class _AIGeneratorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cleanup_callback = None

    def closeEvent(self, event):
        if self._cleanup_callback is not None:
            self._cleanup_callback()
        super().closeEvent(event)

    def reject(self):
        if self._cleanup_callback is not None:
            self._cleanup_callback()
        super().reject()


class CommentTemplateLibraryWidget(QWidget):
    def __init__(self, database, project_id=None, page_id=None, parent=None):
        super().__init__(parent)
        self.service = AutoCommentTemplateService(database)
        self.project_id = project_id
        self.page_id = page_id
        self.ai_dialog = None
        self.editor_dialog = None
        self.setStyleSheet(_TABLE_STYLE)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Tìm mẫu…")
        self.search.textChanged.connect(self.reload)
        self.ai_button = QPushButton("✨ AI tạo mẫu text")
        self.ai_button.clicked.connect(lambda: self.open_ai_generator())
        self.add_button = QPushButton("Thêm mẫu")
        self.add_button.clicked.connect(lambda: self.create_template_dialog())
        self.edit_button = QPushButton("Sửa mẫu")
        self.edit_button.clicked.connect(lambda: self.edit_selected_template())
        self.delete_button = QPushButton("Xóa mẫu")
        self.delete_button.clicked.connect(lambda: self.delete())
        self.refresh_button = QPushButton("Làm mới")
        self.refresh_button.clicked.connect(lambda: self.reload())
        for x in (self.ai_button, self.add_button, self.edit_button, self.delete_button, self.refresh_button, self.search):
            bar.addWidget(x)
        layout.addLayout(bar)
        self.table = _TemplateTable(0, 6)
        self.table.setHorizontalHeaderLabels(["Bật", "Tên mẫu", "Phân loại", "Nội dung", "Placeholder", "Cập nhật"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.itemChanged.connect(self.toggle)
        layout.addWidget(self.table)
        self.reload()

    def set_context(self, project_id, page_id):
        self.project_id = project_id
        self.page_id = page_id
        self.reload()
        if self.ai_dialog is not None and hasattr(self.ai_dialog, "on_context_changed"):
            self.ai_dialog.on_context_changed()

    def reload(self, *_):
        prev_id = self.selected()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        restored_row = -1
        if self.project_id and self.page_id:
            for item in self.service.list_templates(self.project_id, self.page_id):
                if self.search.text().casefold() not in (item["name"] + item["body_text"] + (item["category"] or "")).casefold():
                    continue
                r = self.table.rowCount()
                self.table.insertRow(r)
                check = QTableWidgetItem()
                check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
                check.setCheckState(Qt.CheckState.Checked if item["enabled"] else Qt.CheckState.Unchecked)
                check.setData(Qt.ItemDataRole.UserRole, item["id"])
                self.table.setItem(r, 0, check)
                for c, key in enumerate(("name", "category", "body_text"), 1):
                    self.table.setItem(r, c, QTableWidgetItem(item[key] or ""))
                self.table.setItem(r, 4, QTableWidgetItem("{affiliate_url}" if "{affiliate_url}" in item["body_text"] else "—"))
                self.table.setItem(r, 5, QTableWidgetItem(item["updated_at"]))
                if prev_id and item["id"] == prev_id:
                    restored_row = r
        self.table.blockSignals(False)
        if restored_row >= 0:
            self.table.setCurrentCell(restored_row, 0)

    def selected(self):
        row = self.table.currentRow()
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 and self.table.item(row, 0) else None

    def toggle(self, item):
        if item.column() == 0:
            try:
                self.service.set_template_enabled(
                    self.project_id, self.page_id, item.data(Qt.ItemDataRole.UserRole), item.checkState() == Qt.CheckState.Checked
                )
            except TemplateError:
                self.reload()

    def create_template_dialog(self):
        return self._editor(None)

    def edit_selected_template(self):
        tid = self.selected()
        if tid:
            return self._editor(tid)
        return None

    def create_editor_dialog(self, tid=None):
        old = self.service.get_template(self.project_id, self.page_id, tid) if tid else {}
        d = QDialog(self)
        d.setWindowTitle("Mẫu comment")
        f = QFormLayout(d)
        name = QLineEdit(old.get("name", ""))
        cat = QLineEdit(old.get("category", "") or "")
        body = QPlainTextEdit(old.get("body_text", ""))
        enabled = QCheckBox("Bật mẫu")
        enabled.setChecked(old.get("enabled", True))
        f.addRow("Tên mẫu", name)
        f.addRow("Phân loại", cat)
        f.addRow("Nội dung", body)
        f.addRow(enabled)
        f.addRow(QLabel("Có thể dùng {affiliate_url} để đánh dấu vị trí chèn link ở Phase 3."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        f.addRow(buttons)
        buttons.rejected.connect(d.reject)

        def save():
            try:
                if tid:
                    self.service.update_template(
                        self.project_id, self.page_id, tid, name.text(), body.toPlainText(), cat.text(), enabled.isChecked()
                    )
                else:
                    self.service.create_template(
                        self.project_id, self.page_id, name.text(), body.toPlainText(), cat.text(), enabled.isChecked()
                    )
                d.accept()
                self.reload()
            except TemplateError as exc:
                QMessageBox.warning(d, "Mẫu comment", str(exc))

        buttons.accepted.connect(save)
        d.name_edit = name
        d.category_edit = cat
        d.body_edit = body
        d.enabled_checkbox = enabled
        d.button_box = buttons
        d.save_template = save
        self.editor_dialog = d
        return d

    def _editor(self, tid):
        d = self.create_editor_dialog(tid)
        d.exec()
        return d

    def delete(self):
        tid = self.selected()
        if tid and QMessageBox.question(
            self, "Xóa mẫu", "Xóa mẫu đã chọn?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            try:
                self.service.delete_template(self.project_id, self.page_id, tid)
                self.reload()
            except TemplateError:
                pass

    def create_ai_generator_dialog(self):
        if not self.project_id or not self.page_id:
            QMessageBox.information(self, "Kho mẫu comment", "Hãy chọn Fanpage trước khi tạo mẫu AI.")
            return None
        captured_context = (self.project_id, self.page_id)
        captured_project_id, captured_page_id = captured_context
        d = _AIGeneratorDialog(self)
        d.setStyleSheet(_TABLE_STYLE)
        d.setWindowTitle("✨ AI tạo mẫu text")
        d.setMinimumSize(900, 650)
        layout = QVBoxLayout(d)
        form = QFormLayout()
        context = QPlainTextEdit()
        category = QLineEdit()
        tone = QComboBox()
        tone.addItems(["Tự nhiên", "Thân thiện", "Ngắn gọn"])
        count = QComboBox()
        count.addItems(["3", "5", "10"])
        placeholder = QCheckBox("Có placeholder {affiliate_url}")
        for label, field in (
            ("Chủ đề / ngữ cảnh", context),
            ("Phân loại", category),
            ("Phong cách", tone),
            ("Số mẫu", count),
        ):
            form.addRow(label, field)
        layout.addLayout(form)
        layout.addWidget(placeholder)
        results = _TemplateTable(0, 6)
        results.setHorizontalHeaderLabels(["Chọn", "Tên mẫu", "Phân loại", "Nội dung mẫu", "Placeholder", "Cảnh báo"])
        results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = results.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        counts_label = QLabel("Đã tạo: 0 · Đã duyệt: 0 · Đã lưu: 0")
        status = QLabel()
        go = LoadingButton("✨ Xử lý AI")
        save_button = QPushButton("Lưu mẫu đã chọn")
        save_button.setEnabled(False)
        close = QPushButton("Đóng")

        saved_draft_ids: set[str] = set()
        state = {"worker": None, "saving": False, "updating": False}

        def commit_pending_edits():
            if results.currentItem() is not None:
                results.closePersistentEditor(results.currentItem())
            for editor in results.viewport().findChildren(QLineEdit):
                idx = results.currentIndex()
                if not idx.isValid():
                    idx = results.indexAt(editor.pos())
                if idx.isValid():
                    results.itemDelegate(idx).setModelData(editor, results.model(), idx)
                results.itemDelegate().commitData.emit(editor)
                results.itemDelegate().closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
                editor.deleteLater()

        def row_warning(row_idx: int, name_val: str, body_val: str) -> str:
            try:
                _validate(name_val, body_val)
            except TemplateError as exc:
                return str(exc)
            clean = name_val.strip().casefold()
            existing_names = {
                str(x["name"]).strip().casefold()
                for x in self.service.list_templates(captured_project_id, captured_page_id)
            }
            if clean in existing_names:
                return "Tên mẫu đã tồn tại trong Fanpage này."
            for other in range(results.rowCount()):
                if other == row_idx:
                    continue
                other_check = results.item(other, 0)
                if other_check is None:
                    continue
                other_id = str(other_check.data(Qt.ItemDataRole.UserRole))
                if other_id in saved_draft_ids:
                    continue
                if results.item(other, 0).checkState() == Qt.CheckState.Checked and results.item(request_row := row_idx, 0).checkState() == Qt.CheckState.Checked:
                    other_name = (results.item(other, 1).text() if results.item(other, 1) else "").strip().casefold()
                    if clean and clean == other_name:
                        return "Tên mẫu bị trùng trong danh sách chọn."
            return ""

        def update_state(*_):
            if state["updating"]:
                return
            state["updating"] = True
            commit_pending_edits()
            results.blockSignals(True)
            try:
                generated = results.rowCount()
                approved = 0
                unsaved_approved = 0
                for r in range(generated):
                    check_item = results.item(r, 0)
                    if check_item is None:
                        continue
                    draft_id = str(check_item.data(Qt.ItemDataRole.UserRole))
                    name_val = results.item(r, 1).text() if results.item(r, 1) else ""
                    body_val = results.item(r, 3).text() if results.item(r, 3) else ""
                    ph_item = results.item(r, 4)
                    if ph_item is None:
                        ph_item = QTableWidgetItem()
                        ph_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                        results.setItem(r, 4, ph_item)
                    new_ph = "Có" if "{affiliate_url}" in body_val else "—"
                    if ph_item.text() != new_ph:
                        ph_item.setText(new_ph)
                    warn_item = results.item(r, 5)
                    if warn_item is None:
                        warn_item = QTableWidgetItem()
                        warn_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                        results.setItem(r, 5, warn_item)
                    new_warn = "Đã lưu" if draft_id in saved_draft_ids else row_warning(r, name_val, body_val)
                    if warn_item.text() != new_warn:
                        warn_item.setText(new_warn)
                    if check_item.checkState() == Qt.CheckState.Checked:
                        approved += 1
                        if draft_id not in saved_draft_ids:
                            unsaved_approved += 1
                saved = len(saved_draft_ids)
                d.generated_count = generated
                d.approved_count = approved
                d.saved_count = saved
                counts_label.setText(f"Đã tạo: {generated} · Đã duyệt: {approved} · Đã lưu: {saved}")
                save_button.setEnabled((not go.is_loading) and (not state["saving"]) and unsaved_approved > 0)
            finally:
                results.blockSignals(False)
                state["updating"] = False

        def on_context_changed():
            if (self.project_id, self.page_id) != captured_context and results.rowCount() > 0:
                status.setText(
                    f"Cảnh báo: Fanpage đang chọn đã đổi sang '{self.page_id or 'chưa chọn'}'. "
                    f"Mẫu trong hộp thoại thuộc Fanpage '{captured_page_id}'; hãy quay lại Fanpage '{captured_page_id}' để lưu hoặc đóng hộp thoại."
                )

        def cleanup_worker():
            worker = state["worker"]
            state["worker"] = None
            go.set_loading(False)
            if worker is not None:
                worker.requestInterruption()
                for sig in (worker.succeeded, worker.failed, worker.finished):
                    try:
                        sig.disconnect()
                    except (RuntimeError, TypeError):
                        pass
                worker.finished.connect(worker.deleteLater)
            update_state()

        def run():
            if go.is_loading or state["worker"] is not None:
                return
            go.set_loading(True)
            update_state()
            status.setText("AI đang tạo mẫu text…")
            ctx_text = context.toPlainText()
            cat_text = category.text()
            cnt_val = int(count.currentText())
            tone_val = tone.currentText()
            ph_val = placeholder.isChecked()
            worker = FacebookRequestWorker(
                lambda: generate_text_templates(ctx_text, cat_text, cnt_val, tone_val, ph_val), d
            )
            state["worker"] = worker

            def ok(items):
                saved_draft_ids.clear()
                state["updating"] = True
                results.blockSignals(True)
                try:
                    results.setRowCount(0)
                    for item in items:
                        r = results.rowCount()
                        results.insertRow(r)
                        check = QTableWidgetItem()
                        check.setFlags(
                            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
                        )
                        check.setCheckState(Qt.CheckState.Unchecked)
                        check.setData(Qt.ItemDataRole.UserRole, str(item["id"]))
                        results.setItem(r, 0, check)
                        for col_idx, key in enumerate(("name", "category", "body_text"), 1):
                            cell = QTableWidgetItem(str(item.get(key) or ""))
                            cell.setFlags(
                                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
                            )
                            results.setItem(r, col_idx, cell)
                        ph_cell = QTableWidgetItem("Có" if item.get("has_affiliate_placeholder") else "—")
                        ph_cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                        results.setItem(r, 4, ph_cell)
                        warn_cell = QTableWidgetItem("")
                        warn_cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                        results.setItem(r, 5, warn_cell)
                finally:
                    results.blockSignals(False)
                    state["updating"] = False
                update_state()
                if (self.project_id, self.page_id) != captured_context:
                    status.setText(
                        f"Đã tạo {len(items)} mẫu nhưng Fanpage đã chuyển sang '{self.page_id or 'chưa chọn'}'. "
                        f"Mẫu thuộc Fanpage '{captured_page_id}'; hãy quay lại Fanpage '{captured_page_id}' để lưu hoặc đóng hộp thoại."
                    )
                else:
                    status.setText(f"Đã tạo {len(items)} mẫu; hãy chọn để duyệt.")

            def fail(msg):
                status.setText(str(msg))

            def done():
                if state["worker"] is worker:
                    state["worker"] = None
                go.set_loading(False)
                update_state()
                worker.deleteLater()

            worker.succeeded.connect(ok)
            worker.failed.connect(fail)
            worker.finished.connect(done)
            worker.start()

        def save():
            if go.is_loading or state["saving"]:
                return
            commit_pending_edits()
            if (self.project_id, self.page_id) != captured_context:
                status.setText(
                    f"Không thể lưu: ngữ cảnh Fanpage đã đổi sang '{self.page_id or 'chưa chọn'}'. "
                    f"Mẫu đang tạo thuộc Fanpage '{captured_page_id}' (Project '{captured_project_id}'); "
                    f"vui lòng quay lại Fanpage '{captured_page_id}' hoặc hủy bản nháp."
                )
                return
            selected = []
            selected_rows = []
            for r in range(results.rowCount()):
                check_item = results.item(r, 0)
                if check_item is None:
                    continue
                draft_id = str(check_item.data(Qt.ItemDataRole.UserRole))
                if draft_id in saved_draft_ids:
                    continue
                if check_item.checkState() == Qt.CheckState.Checked:
                    selected_rows.append((r, draft_id))
                    selected.append(
                        {
                            "id": draft_id,
                            "name": results.item(r, 1).text() if results.item(r, 1) else "",
                            "category": results.item(r, 2).text().strip() if results.item(r, 2) else "",
                            "body_text": results.item(r, 3).text() if results.item(r, 3) else "",
                            "enabled": True,
                        }
                    )
            if not selected:
                status.setText(
                    "Không có mẫu mới nào được chọn để lưu." if saved_draft_ids else "Hãy chọn ít nhất một mẫu để lưu."
                )
                update_state()
                return
            state["saving"] = True
            update_state()
            try:
                self.service.create_templates(captured_project_id, captured_page_id, selected)
            except Exception as exc:
                state["saving"] = False
                update_state()
                status.setText(
                    str(exc)
                    if isinstance(exc, TemplateError)
                    else "Không thể lưu mẫu vào cơ sở dữ liệu. Đã hoàn tác toàn bộ thay đổi."
                )
                return
            state["saving"] = False
            state["updating"] = True
            results.blockSignals(True)
            try:
                for r, draft_id in selected_rows:
                    saved_draft_ids.add(draft_id)
                    results.item(r, 0).setFlags(Qt.ItemFlag.ItemIsSelectable)
                    for col_idx in (1, 2, 3):
                        if results.item(r, col_idx) is not None:
                            results.item(r, col_idx).setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    if results.item(r, 5) is not None:
                        results.item(r, 5).setText("Đã lưu")
            finally:
                results.blockSignals(False)
                state["updating"] = False
            self.reload()
            update_state()
            status.setText(f"Đã lưu {len(selected)} mẫu.")

        d._cleanup_callback = cleanup_worker
        results.itemChanged.connect(update_state)
        save_button.clicked.connect(save)
        go.clicked.connect(run)
        close.clicked.connect(d.reject)
        layout.addWidget(go)
        layout.addWidget(results, 1)
        layout.addWidget(counts_label)
        layout.addWidget(save_button)
        layout.addWidget(status)
        layout.addWidget(close)

        d.captured_context = captured_context
        d.context_edit = context
        d.category_edit = category
        d.tone_combo = tone
        d.count_combo = count
        d.placeholder_checkbox = placeholder
        d.results_table = results
        d.generate_button = go
        d.save_button = save_button
        d.close_button = close
        d.status_label = status
        d.counts_label = counts_label
        d.saved_draft_ids = saved_draft_ids
        d.run_generation = run
        d.save_selected = save
        d.on_context_changed = on_context_changed
        update_state()
        self.ai_dialog = d
        return d

    def open_ai_generator(self):
        d = self.create_ai_generator_dialog()
        if d is not None:
            d.exec()
        return d
