from __future__ import annotations
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMessageBox

from app.database.connection import Database
from app.services.auto_comment_template_service import AutoCommentTemplateService, TemplateError
from app.services.auto_comment_service import CommentItem
from app.services.facebook_publishing_service import FacebookPublishingService
from app.services.project_service import ProjectService
from app.ui.pages.auto_comment_page import AutoCommentPage
from app.ui.widgets.comment_template_library_widget import CommentTemplateLibraryWidget
from app.ui.widgets.comment_composer_widget import CommentComposerDialog


class _ManualWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()
    instances: list["_ManualWorker"] = []

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.started = False
        self.interrupted = False
        _ManualWorker.instances.append(self)

    def start(self):
        self.started = True

    def requestInterruption(self):
        self.interrupted = True


class TemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        _ManualWorker.instances.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "templates_test.sqlite3"
        self.db = Database(self.db_path)
        self.s = AutoCommentTemplateService(self.db)
        pid = ProjectService(self.db).create("Project 1", "p1", str(Path(self.tmp.name) / "v"))
        with self.db.connection:
            self.db.connection.execute("UPDATE projects SET uuid='proj-1' WHERE uuid=?", (pid,))
        FacebookPublishingService(self.db).save_pages(
            "proj-1",
            [{"id": "page-a", "name": "Fanpage A"}, {"id": "page-b", "name": "Fanpage B"}],
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_crud_unicode_and_isolation(self):
        tid = self.s.create_template("proj-1", "page-a", "Mẫu Việt", "Xem {affiliate_url}", "sale")
        self.assertEqual(self.s.get_template("proj-1", "page-a", tid)["body_text"], "Xem {affiliate_url}")
        self.s.update_template("proj-1", "page-a", tid, "Mẫu mới", "Xin chào", enabled=False)
        self.assertFalse(self.s.get_template("proj-1", "page-a", tid)["enabled"])
        self.assertEqual(self.s.list_templates("proj-1", "page-b"), [])
        with self.assertRaises(TemplateError):
            self.s.get_template("proj-1", "page-b", tid)
        self.s.delete_template("proj-1", "page-a", tid)
        self.assertEqual(self.s.list_templates("proj-1", "page-a"), [])

    def test_validation_and_duplicate(self):
        with self.assertRaises(TemplateError):
            self.s.create_template("proj-1", "page-a", "", "x")
        with self.assertRaises(TemplateError):
            self.s.create_template("proj-1", "page-a", "x", "")
        with self.assertRaises(TemplateError):
            self.s.create_template("proj-1", "page-a", "x", "{bad}")
        with self.assertRaises(TemplateError):
            self.s.create_template("proj-1", "page-a", "x", "Xem {affiliate_url")
        self.s.create_template("proj-1", "page-a", "x", "nội dung")
        with self.assertRaises(TemplateError):
            self.s.create_template("proj-1", "page-a", "x", "khác")

    def test_atomic_batch_rolls_back_duplicate(self):
        with self.assertRaises(TemplateError):
            self.s.create_templates(
                "proj-1",
                "page-a",
                [{"name": "a", "body_text": "x"}, {"name": "a", "body_text": "y"}],
            )
        self.assertEqual(self.s.list_templates("proj-1", "page-a"), [])

    def test_end_to_end_ai_template_dialog_save_reopen_and_page_isolation(self):
        page = AutoCommentPage(self.db)
        page._go_to_step(1)

        # 1. Select Fanpage A
        page.page.setCurrentIndex(1)
        self.assertEqual(page.page.currentData(), ("proj-1", "page-a"))

        # 2. Open Kho mẫu comment
        with patch.object(QDialog, "exec", return_value=0):
            QTest.mouseClick(page.open_template_library_button, Qt.MouseButton.LeftButton)
            lib = page.template_library_widget
            self.assertIsNotNone(lib)
            self.assertEqual((lib.project_id, lib.page_id), ("proj-1", "page-a"))

            # 3. Open AI tạo mẫu text
            QTest.mouseClick(lib.ai_button, Qt.MouseButton.LeftButton)
            ai_dlg = lib.ai_dialog
            self.assertIsNotNone(ai_dlg)
            self.assertFalse(ai_dlg.save_button.isEnabled())

        # 4. Enter context & 5. Request 3 templates
        ai_dlg.context_edit.setPlainText("Áo khoác chống nắng mùa hè")
        ai_dlg.category_edit.setText("Thời trang")
        ai_dlg.count_combo.setCurrentText("3")
        ai_dlg.placeholder_checkbox.setChecked(True)

        mock_drafts = [
            {
                "id": "d1",
                "name": "AI 1",
                "category": "Thời trang",
                "body_text": "Mẫu 1 tham khảo {affiliate_url}",
                "has_affiliate_placeholder": True,
                "enabled": False,
            },
            {
                "id": "d2",
                "name": "AI 2",
                "category": "Thời trang",
                "body_text": "Mẫu 2 chất lượng cao",
                "has_affiliate_placeholder": False,
                "enabled": False,
            },
            {
                "id": "d3",
                "name": "AI 3",
                "category": "Thời trang",
                "body_text": "Mẫu 3 ưu đãi {affiliate_url}",
                "has_affiliate_placeholder": True,
                "enabled": False,
            },
        ]

        # 6. Mock three valid results
        with patch("app.ui.widgets.comment_template_library_widget.FacebookRequestWorker", _ManualWorker):
            QTest.mouseClick(ai_dlg.generate_button, Qt.MouseButton.LeftButton)
            self.assertEqual(len(_ManualWorker.instances), 1)
            worker = _ManualWorker.instances[0]
            self.assertTrue(ai_dlg.generate_button.is_loading)
            self.assertFalse(ai_dlg.save_button.isEnabled())
            worker.succeeded.emit(mock_drafts)
            worker.finished.emit()

        # 7. Verify three draft rows appear, all unchecked, Save disabled
        tbl = ai_dlg.results_table
        self.assertEqual(tbl.rowCount(), 3)
        self.assertEqual((ai_dlg.generated_count, ai_dlg.approved_count, ai_dlg.saved_count), (3, 0, 0))
        self.assertFalse(ai_dlg.save_button.isEnabled())
        for r in range(3):
            self.assertEqual(tbl.item(r, 0).checkState(), Qt.CheckState.Unchecked)

        # 8. Edit one name and one body (including active inline editor commit)
        tbl.item(0, 1).setText("Mẫu Áo Khoác 1 Đã Sửa")
        tbl.setCurrentCell(1, 3)
        tbl.editItem(tbl.item(1, 3))
        editors = tbl.viewport().findChildren(QLineEdit)
        if editors:
            editors[0].setText("Nội dung mẫu 2 đã chỉnh sửa tại bảng {affiliate_url}")
        else:
            tbl.item(1, 3).setText("Nội dung mẫu 2 đã chỉnh sửa tại bảng {affiliate_url}")

        # 9. Check exactly two drafts (row 0 and row 1; row 2 remains unchecked)
        tbl.item(0, 0).setCheckState(Qt.CheckState.Checked)
        tbl.item(1, 0).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(ai_dlg.save_button.isEnabled())
        self.assertEqual((ai_dlg.generated_count, ai_dlg.approved_count, ai_dlg.saved_count), (3, 2, 0))

        # 10. Click Lưu mẫu đã chọn
        QTest.mouseClick(ai_dlg.save_button, Qt.MouseButton.LeftButton)

        # 11. Verify# exactly two SQLite records
        saved_records = self.s.list_templates("proj-1", "page-a")
        self.assertEqual(len(saved_records), 2)

        # 12. Verify edited values were saved and counts/UI updated
        by_name = {r["name"]: r for r in saved_records}
        self.assertIn("Mẫu Áo Khoác 1 Đã Sửa", by_name)
        self.assertIn("AI 2", by_name)
        self.assertEqual(
            by_name["AI 2"]["body_text"],
            "Nội dung mẫu 2 đã chỉnh sửa tại bảng {affiliate_url}",
        )
        self.assertNotIn("AI 3", by_name)
        self.assertEqual((ai_dlg.generated_count, ai_dlg.approved_count, ai_dlg.saved_count), (3, 2, 2))
        self.assertFalse(ai_dlg.save_button.isEnabled())
        self.assertIn("Đã lưu 2 mẫu", ai_dlg.status_label.text())
        self.assertEqual(lib.table.rowCount(), 2)

        # 13. Click Save again (both button click and direct invoke)
        QTest.mouseClick(ai_dlg.save_button, Qt.MouseButton.LeftButton)
        ai_dlg.save_selected()

        # 14. Verify no duplicate records
        self.assertEqual(len(self.s.list_templates("proj-1", "page-a")), 2)

        # 15. Close/reopen the Template Library & 16. Verify persistence
        ai_dlg.close()
        page.template_library_dialog.close()
        with patch.object(QDialog, "exec", return_value=0):
            QTest.mouseClick(page.open_template_library_button, Qt.MouseButton.LeftButton)
            reopened_lib = page.template_library_widget
            self.assertEqual(reopened_lib.table.rowCount(), 2)
            reopened_names = {reopened_lib.table.item(r, 1).text() for r in range(reopened_lib.table.rowCount())}
            self.assertEqual(reopened_names, {"Mẫu Áo Khoác 1 Đã Sửa", "AI 2"})

        # 17. Switch to Fanpage B & 18. Verify Page A templates are absent
        page.page.setCurrentIndex(2)
        self.assertEqual(page.page.currentData(), ("proj-1", "page-b"))
        self.assertEqual((reopened_lib.project_id, reopened_lib.page_id), ("proj-1", "page-b"))
        self.assertEqual(reopened_lib.table.rowCount(), 0)
        self.assertEqual(self.s.list_templates("proj-1", "page-b"), [])
        page.close()

    def test_no_approved_drafts_invalid_placeholder_duplicate_and_db_rollback(self):
        lib = CommentTemplateLibraryWidget(self.db, "proj-1", "page-a")
        ai_dlg = lib.create_ai_generator_dialog()
        mock_drafts = [
            {"id": "1", "name": "Mẫu A", "category": "Cat", "body_text": "Nội dung A", "has_affiliate_placeholder": False},
            {"id": "2", "name": "Mẫu B", "category": "Cat", "body_text": "Nội dung B", "has_affiliate_placeholder": False},
        ]
        with patch("app.ui.widgets.comment_template_library_widget.FacebookRequestWorker", _ManualWorker):
            ai_dlg.run_generation()
            _ManualWorker.instances[-1].succeeded.emit(mock_drafts)
            _ManualWorker.instances[-1].finished.emit()

        # No approved drafts
        self.assertFalse(ai_dlg.save_button.isEnabled())
        ai_dlg.save_selected()
        self.assertIn("Hãy chọn ít nhất một mẫu", ai_dlg.status_label.text())
        self.assertEqual(self.s.list_templates("proj-1", "page-a"), [])

        # Check both drafts, introduce invalid placeholder on draft 2 -> atomic rollback
        tbl = ai_dlg.results_table
        tbl.item(0, 0).setCheckState(Qt.CheckState.Checked)
        tbl.item(1, 0).setCheckState(Qt.CheckState.Checked)
        tbl.item(1, 3).setText("Link sai {wrong_placeholder}")
        self.assertIn("affiliate_url", tbl.item(1, 5).text())
        QTest.mouseClick(ai_dlg.save_button, Qt.MouseButton.LeftButton)
        self.assertIn("Chỉ hỗ trợ placeholder {affiliate_url}", ai_dlg.status_label.text())
        self.assertEqual(self.s.list_templates("proj-1", "page-a"), [])
        self.assertEqual(ai_dlg.saved_count, 0)
        self.assertEqual(tbl.item(0, 0).checkState(), Qt.CheckState.Checked)
        self.assertEqual(tbl.item(1, 0).checkState(), Qt.CheckState.Checked)

        # Fix placeholder, but create duplicate name against existing Page A template -> atomic rollback
        tbl.item(1, 3).setText("Link đúng {affiliate_url}")
        self.assertEqual(tbl.item(1, 4).text(), "Có")
        self.s.create_template("proj-1", "page-a", "Đã Tồn Tại", "Nội dung cũ")
        tbl.item(1, 1).setText("Đã Tồn Tại")
        QTest.mouseClick(ai_dlg.save_button, Qt.MouseButton.LeftButton)
        self.assertIn("đã tồn tại", ai_dlg.status_label.text())
        # Only the 1 pre-existing template exists; "Mẫu A" was rolled back!
        self.assertEqual([x["name"] for x in self.s.list_templates("proj-1", "page-a")], ["Đã Tồn Tại"])
        self.assertEqual(ai_dlg.saved_count, 0)

        # Fix duplicate name, simulate mid-batch database failure -> atomic rollback
        tbl.item(1, 1).setText("Mẫu B Hợp Lệ")
        real_conn = self.db.connection
        call_count = {"inserts": 0}

        class _FailingConnectionProxy:
            def __enter__(self_proxy):
                real_conn.__enter__()
                return self_proxy

            def __exit__(self_proxy, exc_type, exc_val, exc_tb):
                return real_conn.__exit__(exc_type, exc_val, exc_tb)

            def execute(self_proxy, sql, params=()):
                if sql.strip().upper().startswith("INSERT"):
                    call_count["inserts"] += 1
                    if call_count["inserts"] == 2:
                        raise sqlite3.OperationalError("Simulated DB write failure on 2nd row")
                return real_conn.execute(sql, params)

        with patch.object(self.db, "connection", _FailingConnectionProxy()):
            QTest.mouseClick(ai_dlg.save_button, Qt.MouseButton.LeftButton)

        self.assertEqual(call_count["inserts"], 2)
        self.assertEqual([x["name"] for x in self.s.list_templates("proj-1", "page-a")], ["Đã Tồn Tại"])
        self.assertEqual(ai_dlg.saved_count, 0)
        self.assertEqual(tbl.item(0, 1).text(), "Mẫu A")
        self.assertEqual(tbl.item(1, 1).text(), "Mẫu B Hợp Lệ")

        # Now save normally -> both succeed atomically
        QTest.mouseClick(ai_dlg.save_button, Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.s.list_templates("proj-1", "page-a")), 3)
        self.assertEqual(ai_dlg.saved_count, 2)
        ai_dlg.close()
        lib.close()

    def test_page_switch_during_generation_and_before_saving(self):
        lib = CommentTemplateLibraryWidget(self.db, "proj-1", "page-a")
        ai_dlg = lib.create_ai_generator_dialog()

        # 1. Switch Page during generation
        with patch("app.ui.widgets.comment_template_library_widget.FacebookRequestWorker", _ManualWorker):
            ai_dlg.run_generation()
            worker = _ManualWorker.instances[-1]
            lib.set_context("proj-1", "page-b")
            worker.succeeded.emit(
                [{"id": "g1", "name": "Mẫu G1", "category": "C", "body_text": "Nội dung G1", "has_affiliate_placeholder": False}]
            )
            worker.finished.emit()

        self.assertIn("page-a", ai_dlg.status_label.text())
        ai_dlg.results_table.item(0, 0).setCheckState(Qt.CheckState.Checked)

        # Clicking Save while on Page B is blocked and does not alter Page B selection
        QTest.mouseClick(ai_dlg.save_button, Qt.MouseButton.LeftButton)
        self.assertIn("page-a", ai_dlg.status_label.text())
        self.assertEqual((lib.project_id, lib.page_id), ("proj-1", "page-b"))
        self.assertEqual(self.s.list_templates("proj-1", "page-a"), [])
        self.assertEqual(self.s.list_templates("proj-1", "page-b"), [])

        # Return to Page A -> Save succeeds; then switch to Page B before saving a 2nd draft
        lib.set_context("proj-1", "page-a")
        with patch("app.ui.widgets.comment_template_library_widget.FacebookRequestWorker", _ManualWorker):
            ai_dlg.run_generation()
            worker = _ManualWorker.instances[-1]
            worker.succeeded.emit(
                [{"id": "g2", "name": "Mẫu G2", "category": "C", "body_text": "Nội dung G2", "has_affiliate_placeholder": False}]
            )
            worker.finished.emit()

        ai_dlg.results_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        # Switch Page before saving
        lib.set_context("proj-1", "page-b")
        QTest.mouseClick(ai_dlg.save_button, Qt.MouseButton.LeftButton)
        self.assertIn("page-a", ai_dlg.status_label.text())
        self.assertEqual(self.s.list_templates("proj-1", "page-a"), [])
        self.assertEqual(self.s.list_templates("proj-1", "page-b"), [])

        # Return to Page A -> Save now succeeds into Page A
        lib.set_context("proj-1", "page-a")
        QTest.mouseClick(ai_dlg.save_button, Qt.MouseButton.LeftButton)
        self.assertEqual([x["name"] for x in self.s.list_templates("proj-1", "page-a")], ["Mẫu G2"])
        self.assertEqual(self.s.list_templates("proj-1", "page-b"), [])
        ai_dlg.close()
        lib.close()

    def test_loading_double_click_close_during_worker_and_checkbox_keyboard(self):
        lib = CommentTemplateLibraryWidget(self.db, "proj-1", "page-a")
        ai_dlg = lib.create_ai_generator_dialog()
        self.assertIn("outline:none", ai_dlg.styleSheet())

        with patch("app.ui.widgets.comment_template_library_widget.FacebookRequestWorker", _ManualWorker):
            # Double-click Generate starts only 1 worker
            QTest.mouseClick(ai_dlg.generate_button, Qt.MouseButton.LeftButton)
            QTest.mouseClick(ai_dlg.generate_button, Qt.MouseButton.LeftButton)
            self.assertEqual(len(_ManualWorker.instances), 1)
            worker = _ManualWorker.instances[0]
            self.assertTrue(ai_dlg.generate_button.is_loading)

            # Close dialog while worker is running
            ai_dlg.close()
            self.assertTrue(worker.interrupted)
            self.assertFalse(ai_dlg.generate_button.is_loading)
            # Emitting after close does not fail
            worker.succeeded.emit([])
            worker.finished.emit()

        # Reopen and verify Space key toggles checkbox in col 0
        ai_dlg2 = lib.create_ai_generator_dialog()
        with patch("app.ui.widgets.comment_template_library_widget.FacebookRequestWorker", _ManualWorker):
            ai_dlg2.run_generation()
            _ManualWorker.instances[-1].succeeded.emit(
                [{"id": "k1", "name": "Mẫu K1", "category": "C", "body_text": "Nội dung K1", "has_affiliate_placeholder": False}]
            )
            _ManualWorker.instances[-1].finished.emit()

        tbl = ai_dlg2.results_table
        tbl.setCurrentCell(0, 0)
        QTest.keyClick(tbl, Qt.Key.Key_Space)
        self.assertEqual(tbl.item(0, 0).checkState(), Qt.CheckState.Checked)
        self.assertTrue(ai_dlg2.save_button.isEnabled())
        ai_dlg2.close()
        lib.close()

    def test_manual_template_crud_and_existing_comment_library_workflow(self):
        lib = CommentTemplateLibraryWidget(self.db, "proj-1", "page-a")

        # Manual Create
        ed = lib.create_editor_dialog(None)
        ed.name_edit.setText("Mẫu Thủ Công 1")
        ed.category_edit.setText("CSKH")
        ed.body_edit.setPlainText("Cảm ơn bạn đã quan tâm {affiliate_url}")
        ed.enabled_checkbox.setChecked(True)
        ed.save_template()
        self.assertEqual(lib.table.rowCount(), 1)
        self.assertEqual(lib.table.item(0, 4).text(), "{affiliate_url}")

        # Manual Edit
        lib.table.setCurrentCell(0, 1)
        ed2 = lib.create_editor_dialog(lib.selected())
        ed2.name_edit.setText("Mẫu Thủ Công 1 Đã Sửa")
        ed2.body_edit.setPlainText("Nội dung không link")
        ed2.save_template()
        self.assertEqual(lib.table.item(0, 1).text(), "Mẫu Thủ Công 1 Đã Sửa")
        self.assertEqual(lib.table.item(0, 4).text(), "—")

        # Toggle enabled in library table
        lib.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        tid = lib.selected()
        self.assertFalse(self.s.get_template("proj-1", "page-a", tid)["enabled"])

        # Manual Delete
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            lib.delete()
        self.assertEqual(lib.table.rowCount(), 0)
        lib.close()

        # Verify existing AutoCommentPage comment-library workflow is unchanged
        page = AutoCommentPage(self.db)
        page._go_to_step(1)
        page.library_name.setText("Thư viện kiểm thử")
        page.add_row()
        page.comments.item(0, 3).setText("Bình luận thông thường")
        page.save_library()
        self.assertIsNotNone(page.library_id)
        self.assertIn("Đã lưu thư viện", page.csv_status.text())
        page.close()

    def test_comment_studio_dialog_layout_presets_and_bulk_actions(self):
        page = AutoCommentPage(self.db)
        page._go_to_step(1)
        page.page.setCurrentIndex(1)
        with patch.object(QDialog, "exec", return_value=0):
            outer = page.open_template_library()
        lib = page.template_library_widget
        ai = lib.create_ai_generator_dialog()
        editor = lib.create_editor_dialog()
        composer = CommentComposerDialog(self.db, "proj-1", "page-a", "Fanpage A")
        for dialog in (outer, ai, editor, composer):
            self.assertTrue(bool(dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint))
            dialog.show()
            dialog.showMaximized()
            self.assertTrue(dialog.isMaximized())
            dialog.showNormal()
            self.assertFalse(dialog.isMaximized())

        expected_default = (
            "Viết mẫu bình luận tự nhiên, thân thiện để giới thiệu sản phẩm hoặc ủng hộ shop. "
            "Không khẳng định trải nghiệm chưa được cung cấp."
        )
        self.assertEqual(ai.preset_combo.currentText(), "Ủng hộ shop / Giới thiệu sản phẩm")
        self.assertEqual(ai.context_edit.toPlainText(), expected_default)
        ai.context_edit.setPlainText("Ngữ cảnh do người dùng nhập")
        ai.tone_combo.setCurrentIndex(1)
        ai.count_combo.setCurrentText("5")
        self.assertEqual(ai.context_edit.toPlainText(), "Ngữ cảnh do người dùng nhập")
        ai.preset_combo.setCurrentText("Khen sản phẩm")
        self.assertIn("khen sản phẩm", ai.context_edit.toPlainText())
        ai.context_edit.setPlainText("Ngữ cảnh cần giữ khi mở lại")
        ai.close()
        ai = lib.create_ai_generator_dialog()
        self.assertEqual(ai.context_edit.toPlainText(), "Ngữ cảnh cần giữ khi mở lại")

        drafts = [
            {"id": "u1", "name": "Mẫu UX 1", "category": "UX", "body_text": "Nội dung 1", "has_affiliate_placeholder": False},
            {"id": "u2", "name": "Mẫu UX 2", "category": "UX", "body_text": "Nội dung 2", "has_affiliate_placeholder": False},
        ]
        with patch("app.ui.widgets.comment_template_library_widget.FacebookRequestWorker", _ManualWorker):
            ai.run_generation()
            _ManualWorker.instances[-1].succeeded.emit(drafts)
            _ManualWorker.instances[-1].finished.emit()
        QTest.mouseClick(ai.select_all_button, Qt.MouseButton.LeftButton)
        self.assertEqual(ai.approved_count, 2)
        self.assertEqual(ai.select_all_button.text(), "Bỏ chọn tất cả")
        QTest.mouseClick(ai.select_all_button, Qt.MouseButton.LeftButton)
        self.assertEqual(ai.approved_count, 0)
        self.assertFalse(ai.save_button.isEnabled())
        ai.select_all_button.click()
        ai.save_selected()
        self.assertEqual(len(self.s.list_templates("proj-1", "page-a")), 2)

        self.assertEqual(lib.table.rowCount(), 2)
        lib.select_all_button.click()
        self.assertEqual(lib.select_all_button.text(), "Bỏ chọn tất cả")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
            lib.delete_selected()
            self.assertIn("2", question.call_args.args[2])
        self.assertEqual(lib.table.rowCount(), 2)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            lib.delete_selected()
        self.assertEqual(lib.table.rowCount(), 0)
        self.assertEqual(self.s.list_templates("proj-1", "page-b"), [])

        for dialog in (ai, editor, composer, outer):
            dialog.close()
        page.close()

    def test_comment_row_checkboxes_delete_exact_rows(self):
        page = AutoCommentPage(self.db)
        page.set_items([
            CommentItem(f"Comment {number}")
            for number in range(4)
        ])
        self.assertEqual(page.comments.columnCount(), 4)
        for row in range(4):
            page.comments.item(row, 0).setCheckState(Qt.CheckState.Checked)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as question:
            page.delete_rows()
            self.assertIn("4", question.call_args.args[2])
        self.assertEqual(page.comments.rowCount(), 0)

        page.set_items([
            CommentItem(f"Comment {number}")
            for number in range(4)
        ])
        page.comments.item(1, 0).setCheckState(Qt.CheckState.Checked)
        page.comments.item(3, 0).setCheckState(Qt.CheckState.Checked)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            page.delete_rows()
        self.assertEqual(page.comments.rowCount(), 4)
        self.assertTrue(all(page.comments.item(row, 1).checkState() == Qt.CheckState.Checked for row in range(4)))
        page.close()


if __name__ == "__main__":
    unittest.main()
