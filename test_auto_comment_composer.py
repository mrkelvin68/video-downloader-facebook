from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.database.connection import Database
from app.services.auto_comment_affiliate_link_service import (
    AutoCommentAffiliateLinkService,
)
from app.services.auto_comment_composer_service import (
    AutoCommentComposerService,
    CommentComposerError,
    evaluate_draft_warnings,
    render_template_with_affiliate_url,
    validate_template_placeholders,
)
from app.services.auto_comment_template_service import AutoCommentTemplateService
from app.services.facebook_publishing_service import FacebookPublishingService
from app.services.logging_service import SETTINGS_APPLICATION, SETTINGS_ORGANIZATION
from app.services.project_service import ProjectService
from app.ui.pages.auto_comment_page import AutoCommentPage
from app.ui.widgets.comment_composer_widget import (
    CommentComposerDialog,
    CommentComposerWidget,
)


class CommentComposerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "composer_test.sqlite3"
        self.db = Database(self.db_path)
        self.template_service = AutoCommentTemplateService(self.db)
        self.link_service = AutoCommentAffiliateLinkService(self.db)
        self.composer_service = AutoCommentComposerService(self.db)

        pid = ProjectService(self.db).create(
            "Project 1", "p1", str(Path(self.tmp.name) / "v")
        )
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE projects SET uuid='proj-1' WHERE uuid=?", (pid,)
            )
        FacebookPublishingService(self.db).save_pages(
            "proj-1",
            [
                {"id": "page-a", "name": "Fanpage A"},
                {"id": "page-b", "name": "Fanpage B"},
            ],
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_01_to_15_end_to_end_composer_workflow_and_page_isolation(self):
        # Seed Page A templates (2 enabled, 1 disabled) and links (2 enabled, 1 disabled)
        t_enabled_id = self.template_service.create_template(
            "proj-1",
            "page-a",
            "Mẫu Áo Khoác Sale",
            "Thông tin sản phẩm chính hãng tại đây: {affiliate_url}",
            "Thời trang",
            enabled=True,
        )
        t_text_only_id = self.template_service.create_template(
            "proj-1",
            "page-a",
            "Mẫu Chào Hỏi",
            "Cảm ơn bạn đã xem video của shop nhé!",
            "CSKH",
            enabled=True,
        )
        self.template_service.create_template(
            "proj-1",
            "page-a",
            "Mẫu Đã Tắt",
            "Mẫu tắt {affiliate_url}",
            "Ẩn",
            enabled=False,
        )

        raw_shopee_url = (
            "https://s.shopee.vn/4L2xYzAbCd?utm_source=fb_reels&sub_id=FanpageA_01#top"
        )
        l_enabled_id = self.link_service.create_link(
            "proj-1",
            "page-a",
            "Áo Khoác Gió 2 Lớp",
            raw_shopee_url,
            "Chống nước nhẹ",
            enabled=True,
        )
        self.link_service.create_link(
            "proj-1",
            "page-a",
            "Link Đã Tắt",
            "https://s.shopee.vn/disabledLink01",
            "Tắt",
            enabled=False,
        )

        # Seed Page B template and link
        t_page_b_id = self.template_service.create_template(
            "proj-1",
            "page-b",
            "Mẫu Fanpage B",
            "Ưu đãi riêng Fanpage B: {affiliate_url}",
            "Sale B",
            enabled=True,
        )
        l_page_b_id = self.link_service.create_link(
            "proj-1",
            "page-b",
            "Sản Phẩm Fanpage B",
            "https://s.shopee.vn/pageBOnly99",
            "Mô tả B",
            enabled=True,
        )

        page = AutoCommentPage(self.db)
        page._go_to_step(1)
        self.assertTrue(hasattr(page, "open_composer_button"))
        self.assertEqual(page.open_composer_button.text(), "Tạo comment từ mẫu")

        # 2. Select Fanpage A
        page.page.setCurrentIndex(1)
        self.assertEqual(page.page.currentData(), ("proj-1", "page-a"))

        # 1. Open Composer
        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            dlg = page.open_comment_composer()
        self.assertIsNotNone(dlg)
        self.assertIsInstance(dlg, CommentComposerDialog)
        self.assertTrue(bool(dlg.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint))
        composer: CommentComposerWidget = page.composer_widget
        self.assertIn("Fanpage A", composer.page_display.text())

        # 3. Verify only enabled templates from Page A are loaded (2 enabled + 1 placeholder item)
        self.assertEqual(composer.template_combo.count(), 3)
        loaded_template_ids = [
            composer.template_combo.itemData(i)
            for i in range(1, composer.template_combo.count())
        ]
        self.assertCountEqual(loaded_template_ids, [t_enabled_id, t_text_only_id])

        # 4. Verify only enabled Affiliate URLs from Page A are loaded (1 enabled + 1 placeholder item)
        self.assertEqual(composer.link_combo.count(), 2)
        loaded_link_ids = [
            composer.link_combo.itemData(i)
            for i in range(1, composer.link_combo.count())
        ]
        self.assertEqual(loaded_link_ids, [l_enabled_id])

        # 5. Select one template
        idx_t = composer.template_combo.findData(t_enabled_id)
        composer.template_combo.setCurrentIndex(idx_t)
        self.assertEqual(
            composer.template_preview.toPlainText(),
            "Thông tin sản phẩm chính hãng tại đây: {affiliate_url}",
        )

        # 6. Select one URL
        idx_l = composer.link_combo.findData(l_enabled_id)
        composer.link_combo.setCurrentIndex(idx_l)
        self.assertEqual(composer.link_preview.text(), raw_shopee_url)

        # 7. Click Tạo bản nháp (and verify zero AI or Facebook network calls)
        with (
            patch("app.services.ai_comment_service.generate_comment_drafts") as mock_ai,
            patch(
                "app.services.auto_comment_comment_service.AutoCommentCommentService.publish_one"
            ) as mock_publish,
        ):
            QTest.mouseClick(composer.compose_button, Qt.MouseButton.LeftButton)
            mock_ai.assert_not_called()
            mock_publish.assert_not_called()

        # 8. Verify placeholder is replaced correctly
        expected_text = f"Thông tin sản phẩm chính hãng tại đây: {raw_shopee_url}"
        self.assertEqual(composer.composed_preview.toPlainText(), expected_text)
        self.assertIsNotNone(composer.current_draft)
        self.assertEqual(composer.current_draft.template_id, t_enabled_id)
        self.assertEqual(composer.current_draft.affiliate_link_id, l_enabled_id)
        self.assertIn("Mẫu Áo Khoác Sale", composer.source_info_label.text())
        self.assertIn("Áo Khoác Gió 2 Lớp", composer.source_info_label.text())

        # 9. Verify original URL remains unchanged in SQLite and in memory
        saved_link = self.link_service.get_link("proj-1", "page-a", l_enabled_id)
        self.assertEqual(saved_link["url"], raw_shopee_url)
        self.assertEqual(composer.current_draft.affiliate_url, raw_shopee_url)

        # 10. Edit preview text locally
        edited_text = f"{expected_text} — Freeship hôm nay!"
        composer.composed_preview.setPlainText(edited_text)
        self.assertEqual(composer.current_draft.edited_text, edited_text)
        self.assertIn("chỉnh sửa cục bộ", composer.status_label.text())

        # 11. Confirm no SQLite comment-library row is added
        lib_count = self.db.connection.execute(
            "SELECT COUNT(*) FROM auto_comment_libraries"
        ).fetchone()[0]
        item_count = self.db.connection.execute(
            "SELECT COUNT(*) FROM auto_comment_library_items"
        ).fetchone()[0]
        self.assertEqual(lib_count, 0)
        self.assertEqual(item_count, 0)
        self.assertEqual(page.comments.rowCount(), 0)

        # 12. Switch to Fanpage B while Composer dialog is open
        page.page.setCurrentIndex(2)
        self.assertEqual(page.page.currentData(), ("proj-1", "page-b"))

        # 13. Confirm Page A templates and links are invalidated and cannot be used
        self.assertFalse(composer.compose_button.isEnabled())
        self.assertFalse(composer.template_combo.isEnabled())
        self.assertFalse(composer.link_combo.isEnabled())
        self.assertIsNone(composer.current_draft)
        self.assertIn("Fanpage đang chọn đã thay đổi", composer.status_label.text())
        self.assertIsNone(composer.compose_draft())
        dlg.close()

        # 14. Reopen Composer on Fanpage B & 15. Confirm context is Fanpage B
        with patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            dlg_b = page.open_comment_composer()
        composer_b: CommentComposerWidget = page.composer_widget
        self.assertIn("Fanpage B", composer_b.page_display.text())
        self.assertEqual(
            [
                composer_b.template_combo.itemData(i)
                for i in range(1, composer_b.template_combo.count())
            ],
            [t_page_b_id],
        )
        self.assertEqual(
            [
                composer_b.link_combo.itemData(i)
                for i in range(1, composer_b.link_combo.count())
            ],
            [l_page_b_id],
        )
        dlg_b.close()
        page.close()

    def test_additional_validation_edge_cases_and_safety_guard(self):
        # Create valid template with placeholder, template without placeholder, and valid link
        t_with_ph = self.template_service.create_template(
            "proj-1",
            "page-a",
            "Mẫu Có Link",
            "Đặt mua tại: {affiliate_url}",
            enabled=True,
        )
        t_no_ph = self.template_service.create_template(
            "proj-1",
            "page-a",
            "Mẫu Không Placeholder",
            "Inbox shop để được tư vấn size chuẩn nhé!",
            enabled=True,
        )
        l_valid = self.link_service.create_link(
            "proj-1",
            "page-a",
            "Giày Thể Thao",
            "https://s.shopee.vn/shoesValid01",
            enabled=True,
        )
        l_page_b = self.link_service.create_link(
            "proj-1",
            "page-b",
            "Link Page B",
            "https://s.shopee.vn/pageBLink",
            enabled=True,
        )

        # Case: No template selected
        composer = CommentComposerWidget(self.db, "proj-1", "page-a", "Fanpage A")
        composer.template_combo.setCurrentIndex(0)
        self.assertIsNone(composer.compose_draft())
        self.assertIn("Hãy chọn một mẫu comment", composer.status_label.text())

        # Case: Template with placeholder but no link selected
        composer.template_combo.setCurrentIndex(
            composer.template_combo.findData(t_with_ph)
        )
        composer.link_combo.setCurrentIndex(0)
        self.assertIsNone(composer.compose_draft())
        self.assertIn("hãy chọn một Affiliate URL", composer.status_label.text())

        # Case: Template without placeholder produces text-only draft (even if no link or if link is selected, never appends URL)
        composer.template_combo.setCurrentIndex(
            composer.template_combo.findData(t_no_ph)
        )
        composer.link_combo.setCurrentIndex(0)
        draft_text_only = composer.compose_draft()
        self.assertIsNotNone(draft_text_only)
        self.assertEqual(
            draft_text_only.composed_text,
            "Inbox shop để được tư vấn size chuẩn nhé!",
        )

        # When a link is also selected with a non-placeholder template, it does NOT append the URL
        composer.link_combo.setCurrentIndex(composer.link_combo.findData(l_valid))
        draft_no_append = composer.compose_draft()
        self.assertIsNotNone(draft_no_append)
        self.assertEqual(
            draft_no_append.composed_text,
            "Inbox shop để được tư vấn size chuẩn nhé!",
        )
        self.assertNotIn("https://", draft_no_append.composed_text)
        self.assertIn("không tự động chèn link", composer.warning_label.text())

        # Case: Cross-page link or template rejected by service
        with self.assertRaises(CommentComposerError):
            self.composer_service.compose_draft(
                "proj-1", "page-a", t_with_ph, l_page_b
            )

        # Case: Disabled template / Disabled link rejected if disabled after loading
        composer.template_combo.setCurrentIndex(
            composer.template_combo.findData(t_with_ph)
        )
        composer.link_combo.setCurrentIndex(composer.link_combo.findData(l_valid))
        self.link_service.set_link_enabled("proj-1", "page-a", l_valid, False)
        self.assertIsNone(composer.compose_draft())
        self.assertIn("đã bị tắt", composer.status_label.text())

        self.link_service.set_link_enabled("proj-1", "page-a", l_valid, True)
        self.template_service.set_template_enabled("proj-1", "page-a", t_with_ph, False)
        self.assertIsNone(composer.compose_draft())
        self.assertIn("đã bị tắt", composer.status_label.text())
        self.template_service.set_template_enabled("proj-1", "page-a", t_with_ph, True)

        # Case: Deleted template or link rejected cleanly
        t_temp = self.template_service.create_template(
            "proj-1", "page-a", "Mẫu Tạm", "Xem {affiliate_url}"
        )
        composer.reload_options()
        composer.template_combo.setCurrentIndex(composer.template_combo.findData(t_temp))
        composer.link_combo.setCurrentIndex(composer.link_combo.findData(l_valid))
        self.template_service.delete_template("proj-1", "page-a", t_temp)
        self.assertIsNone(composer.compose_draft())
        self.assertIn("không tồn tại", composer.status_label.text())

        # Case: Unsupported placeholder rejected (e.g. legacy/corrupt row in DB or direct render call)
        with self.assertRaises(CommentComposerError):
            validate_template_placeholders("Xem tại {product_link}")
        with self.assertRaises(CommentComposerError):
            render_template_with_affiliate_url(
                "Xem tại {unclosed", "https://s.shopee.vn/shoesValid01"
            )
        t_corrupt = self.template_service.create_template(
            "proj-1", "page-a", "Mẫu Lỗi DB", "Hợp lệ ban đầu"
        )
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE auto_comment_templates SET body_text=? WHERE id=?",
                ("Placeholder lạ {other_url}", t_corrupt),
            )
        with self.assertRaises(CommentComposerError):
            self.composer_service.compose_draft(
                "proj-1", "page-a", t_corrupt, l_valid
            )

        # Case: Safety Guard duplicate URL & duplicate promotional content warning
        t_double = self.template_service.create_template(
            "proj-1",
            "page-a",
            "Mẫu Lặp Link",
            "Link 1: {affiliate_url} và Link 2: {affiliate_url}",
        )
        composer_guard = CommentComposerWidget(
            self.db,
            "proj-1",
            "page-a",
            "Fanpage A",
            existing_comments_provider=lambda: [
                "Đặt mua tại: https://s.shopee.vn/shoesValid01"
            ],
        )
        composer_guard.template_combo.setCurrentIndex(
            composer_guard.template_combo.findData(t_double)
        )
        composer_guard.link_combo.setCurrentIndex(
            composer_guard.link_combo.findData(l_valid)
        )
        draft_dup_url = composer_guard.compose_draft()
        self.assertIsNotNone(draft_dup_url)
        self.assertTrue(
            any("lặp lại" in w for w in draft_dup_url.warnings),
            msg=f"Expected duplicate URL warning, got {draft_dup_url.warnings}",
        )
        self.assertIn("lặp lại", composer_guard.warning_label.text())

        # Also check duplicate against existing Step 2 comments via Safety Guard
        composer_guard.template_combo.setCurrentIndex(
            composer_guard.template_combo.findData(t_with_ph)
        )
        draft_dup_existing = composer_guard.compose_draft()
        self.assertIsNotNone(draft_dup_existing)
        self.assertTrue(
            any("trùng" in w or "lặp lại" in w for w in draft_dup_existing.warnings)
        )
        composer.close()
        composer_guard.close()

    def test_existing_step2_controls_and_ai_generator_unchanged(self):
        page = AutoCommentPage(self.db)
        page._go_to_step(1)

        # Without selecting a page, clicking Tạo comment từ mẫu shows Vietnamese message
        page.page.setCurrentIndex(0)
        with patch.object(QMessageBox, "information") as mock_info:
            self.assertIsNone(page.open_comment_composer())
            mock_info.assert_called_once()
            self.assertIn("Hãy chọn Fanpage", mock_info.call_args[0][2])

        # Verify all Step 2 buttons remain present
        buttons = [
            b.text()
            for b in page.tabs.widget(1).findChildren(type(page.open_composer_button))
        ]
        for expected in (
            "✨ AI tạo comment",
            "Import CSV",
            "Thêm dòng",
            "Xóa đã chọn",
            "Lưu thư viện",
            "Xóa import",
            "Kho mẫu comment",
            "Kho link AFF",
            "Tạo comment từ mẫu",
        ):
            self.assertIn(expected, buttons)

        # Verify AI Generator prompt when unconfigured still works
        settings = QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION)
        old_model = settings.value("ai/model", "", type=str)
        settings.setValue("ai/model", "")
        try:
            with patch.object(QMessageBox, "information") as mock_ai_info:
                page.open_ai_comment_generator()
                mock_ai_info.assert_called_once()
        finally:
            settings.setValue("ai/model", old_model)

        page.close()


if __name__ == "__main__":
    unittest.main()
