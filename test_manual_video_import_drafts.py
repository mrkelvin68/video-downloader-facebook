import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
)

from app.database.connection import Database
from app.services.download_queue_service import DownloadQueueService
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.ui.dialogs.manual_video_import_dialog import ManualVideoImportDialog


class ManualVideoImportDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def dialog(self, project_id="project-a", rows=None):
        return ManualVideoImportDialog(
            rows=rows, project_id=project_id, draft_directory=self.directory
        )

    def reopen(self, project_id="project-a"):
        with patch.object(QMessageBox, "information"):
            return self.dialog(project_id)

    def test_table_has_two_columns_and_no_removed_controls(self):
        dialog = self.dialog()
        self.assertEqual(dialog.TITLE_COLUMN, 0)
        self.assertEqual(dialog.URL_COLUMN, 1)
        self.assertEqual(dialog.table.columnCount(), 2)
        self.assertEqual(
            [dialog.table.horizontalHeaderItem(i).text() for i in range(dialog.table.columnCount())],
            ["Title", "URL"],
        )
        self.assertIsNone(dialog.table.item(0, 2))
        self.assertFalse(hasattr(dialog, "url_paste"))
        self.assertFalse(hasattr(dialog, "add_pasted_urls"))
        self.assertFalse(hasattr(dialog, "_update_platform"))
        self.assertEqual(dialog.findChildren(QPlainTextEdit), [])
        button_texts = [btn.text() for btn in dialog.findChildren(QPushButton)]
        self.assertNotIn("Thêm URL vào danh sách", button_texts)
        self.assertIn("＋ Thêm dòng", button_texts)
        self.assertNotIn("Duplicate", button_texts)
        self.assertIn("Xóa dòng", button_texts)
        self.assertIn("Hủy", button_texts)
        self.assertIn("Lưu", button_texts)
        self.assertTrue(bool(dialog.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint))
        dialog.reject()

    def test_add_delete_and_edit_rows(self):
        dialog = self.dialog()
        self.assertEqual(dialog.table.rowCount(), 1)
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Video 1")
        dialog.table.item(0, dialog.URL_COLUMN).setText("https://www.facebook.com/reel/111")
        dialog.add_row()
        self.assertEqual(dialog.table.rowCount(), 2)
        dialog.table.item(1, dialog.TITLE_COLUMN).setText("Video 2")
        dialog.table.item(1, dialog.URL_COLUMN).setText("https://youtu.be/jNQXAC9IVRw")

        dialog.table.selectRow(0)
        dialog.delete_rows()
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.table.item(0, dialog.TITLE_COLUMN).text(), "Video 2")
        self.assertEqual(dialog.table.item(0, dialog.URL_COLUMN).text(), "https://youtu.be/jNQXAC9IVRw")
        self.assertEqual(dialog.table.item(1, dialog.TITLE_COLUMN).text(), "")
        dialog.reject()

    def test_editing_last_row_appends_one_empty_row(self):
        dialog = self.dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Dòng gốc")
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.table.item(1, dialog.TITLE_COLUMN).text(), "")
        self.assertEqual(dialog.table.item(1, dialog.URL_COLUMN).text(), "")
        dialog.table.item(1, dialog.URL_COLUMN).setText("https://youtu.be/jNQXAC9IVRw")
        self.assertEqual(dialog.table.rowCount(), 3)
        self.assertEqual(dialog.table.item(2, dialog.TITLE_COLUMN).text(), "")
        self.assertEqual(dialog.table.item(2, dialog.URL_COLUMN).text(), "")
        dialog.reject()

    def test_url_first_and_clearing_last_row_keeps_one_empty_row(self):
        dialog = self.dialog()
        dialog.table.item(0, dialog.URL_COLUMN).setText("https://example.com/a")
        self.assertEqual(dialog.table.rowCount(), 2)
        dialog.table.item(1, dialog.TITLE_COLUMN).setText("Tạm thời")
        self.assertEqual(dialog.table.rowCount(), 3)

        dialog.table.item(1, dialog.TITLE_COLUMN).setText("")

        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.table.item(0, dialog.URL_COLUMN).text(), "https://example.com/a")
        self.assertEqual(dialog.table.item(1, dialog.TITLE_COLUMN).text(), "")
        self.assertEqual(dialog.table.item(1, dialog.URL_COLUMN).text(), "")
        dialog.reject()

    def test_double_click_edits_without_copying_or_appending_rows(self):
        dialog = self.dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Video A")
        dialog.table.item(0, dialog.URL_COLUMN).setText("https://example.com/a")
        dialog.show()
        self.app.processEvents()

        for row in (1, 0, 1):
            item = dialog.table.item(row, dialog.TITLE_COLUMN)
            position = dialog.table.visualItemRect(item).center()
            QTest.mouseClick(
                dialog.table.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                position,
            )
            QTest.mouseDClick(
                dialog.table.viewport(),
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                position,
            )
            self.app.processEvents()
            self.assertIsInstance(self.app.focusWidget(), QLineEdit)
            self.assertEqual(dialog.table.rowCount(), 2)
            self.assertEqual(dialog.table.item(0, dialog.TITLE_COLUMN).text(), "Video A")
            self.assertEqual(dialog.table.item(0, dialog.URL_COLUMN).text(), "https://example.com/a")
            self.assertEqual(dialog.table.item(1, dialog.TITLE_COLUMN).text(), "")
            QTest.keyClick(self.app.focusWidget(), Qt.Key.Key_Escape)

        dialog.reject()

    def test_keyboard_navigation_keeps_one_empty_trailing_row(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        dialog.table.setCurrentCell(0, dialog.TITLE_COLUMN)
        dialog.table.editItem(dialog.table.item(0, dialog.TITLE_COLUMN))
        QTest.keyClicks(self.app.focusWidget(), "Video A")
        QTest.keyClick(self.app.focusWidget(), Qt.Key.Key_Tab)
        self.app.processEvents()

        self.assertEqual(dialog.table.item(0, dialog.TITLE_COLUMN).text(), "Video A")
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.table.currentRow(), 0)
        self.assertEqual(dialog.table.currentColumn(), dialog.URL_COLUMN)

        QTest.keyClicks(self.app.focusWidget(), "https://example.com/a")
        QTest.keyClick(self.app.focusWidget(), Qt.Key.Key_Return)
        self.app.processEvents()

        self.assertEqual(dialog.table.item(0, dialog.URL_COLUMN).text(), "https://example.com/a")
        self.assertEqual(dialog.table.currentRow(), 1)
        self.assertEqual(dialog.table.currentColumn(), dialog.URL_COLUMN)
        self.assertEqual(dialog.table.rowCount(), 2)
        QTest.keyClick(dialog.table, Qt.Key.Key_Backtab)
        self.assertEqual(dialog.table.currentRow(), 1)
        self.assertEqual(dialog.table.currentColumn(), dialog.TITLE_COLUMN)
        dialog.reject()

    def test_pasting_one_hundred_rows_has_one_empty_trailing_row(self):
        dialog = self.dialog()
        pasted = dialog.paste_from_clipboard(
            "\n".join(
                f"Video {number}\thttps://example.com/{number}"
                for number in range(100)
            )
        )
        self.assertEqual(pasted, 100)
        self.assertEqual(dialog.table.rowCount(), 101)
        self.assertEqual(dialog.table.item(99, dialog.TITLE_COLUMN).text(), "Video 99")
        self.assertEqual(dialog.table.item(100, dialog.TITLE_COLUMN).text(), "")
        self.assertEqual(dialog.table.item(100, dialog.URL_COLUMN).text(), "")
        dialog.reject()

    def test_single_and_multi_url_clipboard_paste_preserve_existing_titles(self):
        dialog = self.dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Giữ tiêu đề")
        dialog.table.setCurrentCell(0, dialog.TITLE_COLUMN)
        pasted = dialog.paste_from_clipboard("https://youtu.be/jNQXAC9IVRw")
        self.assertEqual(pasted, 1)
        self.assertEqual(dialog.table.item(0, dialog.TITLE_COLUMN).text(), "Giữ tiêu đề")
        self.assertEqual(dialog.table.item(0, dialog.URL_COLUMN).text(), "https://youtu.be/jNQXAC9IVRw")

        dialog.add_row()
        dialog.table.item(1, dialog.TITLE_COLUMN).setText("Dòng 2")
        dialog.table.setCurrentCell(1, dialog.URL_COLUMN)
        multi_urls = (
            "https://www.facebook.com/reel/123456\n"
            "https://www.instagram.com/reel/ABC123/\n"
            "https://www.youtube.com/watch?v=abc123XYZ09"
        )
        pasted_multi = dialog.paste_from_clipboard(multi_urls)
        self.assertEqual(pasted_multi, 3)
        self.assertEqual(dialog.table.rowCount(), 5)
        self.assertEqual(dialog.table.item(1, dialog.TITLE_COLUMN).text(), "Dòng 2")
        self.assertEqual(dialog.table.item(1, dialog.URL_COLUMN).text(), "https://www.facebook.com/reel/123456")
        self.assertEqual(dialog.table.item(2, dialog.URL_COLUMN).text(), "https://www.instagram.com/reel/ABC123/")
        self.assertEqual(dialog.table.item(3, dialog.URL_COLUMN).text(), "https://www.youtube.com/watch?v=abc123XYZ09")
        dialog.reject()

    def test_tsv_and_ctrl_v_clipboard_paste_expand_rows_in_order(self):
        dialog = self.dialog()
        tsv = (
            "Title\tURL\n"
            "FB Clip\thttps://www.facebook.com/reel/111111\n"
            "YT Clip\thttps://youtu.be/jNQXAC9IVRw\n"
            "IG Clip\thttps://www.instagram.com/reel/ABC123/\n"
        )
        QApplication.clipboard().setText(tsv)
        dialog.table.setCurrentCell(0, dialog.TITLE_COLUMN)
        dialog.paste_from_clipboard()
        self.assertEqual(dialog.table.rowCount(), 4)
        self.assertEqual(
            [
                (
                    dialog.table.item(r, dialog.TITLE_COLUMN).text(),
                    dialog.table.item(r, dialog.URL_COLUMN).text(),
                )
                for r in range(3)
            ],
            [
                ("FB Clip", "https://www.facebook.com/reel/111111"),
                ("YT Clip", "https://youtu.be/jNQXAC9IVRw"),
                ("IG Clip", "https://www.instagram.com/reel/ABC123/"),
            ],
        )
        dialog.reject()

    def test_close_and_escape_restore_incomplete_rows_in_order(self):
        dialog = self.dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Tiêu đề đang nhập")
        dialog.table.item(0, dialog.URL_COLUMN).setText("https://invalid.example/partial")
        dialog.add_row()
        dialog.table.item(1, dialog.URL_COLUMN).setText("https://youtu.be/jNQXAC9IVRw")
        dialog.add_row()
        dialog.save_draft()
        dialog.reject()

        restored = self.reopen()
        self.assertEqual(restored.table.rowCount(), 3)
        self.assertEqual(restored.table.item(0, restored.TITLE_COLUMN).text(), "Tiêu đề đang nhập")
        self.assertEqual(restored.table.item(0, restored.URL_COLUMN).text(), "https://invalid.example/partial")
        self.assertEqual(restored.table.item(1, restored.TITLE_COLUMN).text(), "")
        self.assertEqual(restored.table.item(1, restored.URL_COLUMN).text(), "https://youtu.be/jNQXAC9IVRw")
        self.assertEqual(restored.table.item(2, restored.TITLE_COLUMN).text(), "")
        self.assertEqual(restored.table.item(2, restored.URL_COLUMN).text(), "")
        restored.reject()

    def test_legacy_draft_with_platform_and_paste_text_restores_cleanly(self):
        path = self.directory / "manual-video-drafts" / "project-a.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy_payload = {
            "rows": [
                {
                    "ten_video": "Legacy FB",
                    "url": "https://www.facebook.com/reel/111",
                    "platform": "Facebook",
                },
                {
                    "title": "Legacy YT",
                    "url": "https://youtu.be/jNQXAC9IVRw",
                    "platform": "YouTube",
                },
            ],
            "paste_text": "https://www.instagram.com/reel/ABC123/",
        }
        path.write_text(json.dumps(legacy_payload, ensure_ascii=False), encoding="utf-8")

        restored = self.reopen()
        self.assertTrue(path.is_file())
        self.assertEqual(restored.table.columnCount(), 2)
        self.assertEqual(restored.table.rowCount(), 3)
        self.assertEqual(restored.table.item(0, restored.TITLE_COLUMN).text(), "Legacy FB")
        self.assertEqual(restored.table.item(0, restored.URL_COLUMN).text(), "https://www.facebook.com/reel/111")
        self.assertEqual(restored.table.item(1, restored.TITLE_COLUMN).text(), "Legacy YT")
        self.assertEqual(restored.table.item(1, restored.URL_COLUMN).text(), "https://youtu.be/jNQXAC9IVRw")
        restored.reject()

    def test_edit_delete_and_project_isolation_persist(self):
        dialog = self.dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Cũ")
        dialog.add_row()
        dialog.table.item(1, dialog.TITLE_COLUMN).setText("Giữ lại")
        dialog.table.selectRow(0)
        dialog.delete_rows()
        dialog.save_draft()

        restored = self.reopen()
        self.assertEqual(restored.table.rowCount(), 2)
        self.assertEqual(restored.table.item(0, restored.TITLE_COLUMN).text(), "Giữ lại")
        other = self.reopen("project-b")
        self.assertEqual(other.table.rowCount(), 1)
        self.assertEqual(other.table.item(0, other.TITLE_COLUMN).text(), "")
        restored.reject()
        other.reject()

    def test_cancel_discards_only_after_confirmation(self):
        dialog = self.dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Nháp")
        dialog.save_draft()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            dialog._discard_draft()
        self.assertTrue(dialog.isVisible() or dialog.result() == 0)
        self.assertTrue(dialog._draft_path.is_file())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._discard_draft()
        self.assertFalse(dialog._draft_path.exists())

    def test_save_commits_all_platforms_and_db_failure_preserves_draft(self):
        dialog = self.dialog()
        dialog.paste_from_clipboard(
            "FB Video\thttps://www.facebook.com/reel/111\n"
            "YT Video\thttps://youtu.be/jNQXAC9IVRw\n"
            "IG Video\thttps://www.instagram.com/reel/ABC123/"
        )
        dialog.save_draft()
        dialog.save_rows()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        self.assertTrue(dialog._draft_path.is_file())

        db = Database(self.directory / "test.sqlite3")
        try:
            project_id = ProjectService(db).create("Demo", "d", str(self.directory / "videos"))
            video_service = VideoService(db)
            with patch.object(
                db, "append_videos", side_effect=sqlite3.OperationalError("db locked")
            ):
                with self.assertRaises(sqlite3.OperationalError):
                    video_service.import_csv(project_id, dialog.csv_text, append=True)
            self.assertTrue(dialog._draft_path.is_file())

            imported = video_service.import_csv(project_id, dialog.csv_text, append=True)
            self.assertEqual(imported, 3)
            dialog.clear_draft()
            self.assertFalse(dialog._draft_path.exists())

            videos = db.list_videos(project_id)
            self.assertEqual([v["platform"] for v in videos], ["facebook", "youtube", "instagram"])
            queue = DownloadQueueService(db)
            enqueued = queue.enqueue(project_id, [v["id"] for v in videos])
            self.assertEqual(len(enqueued), 3)
        finally:
            db.close()

    def test_duplicate_canonical_urls_rejected_on_save(self):
        dialog = self.dialog()
        dialog.paste_from_clipboard(
            "YT Shortlink\thttps://youtu.be/jNQXAC9IVRw\n"
            "YT Watch\thttps://www.youtube.com/watch?v=jNQXAC9IVRw"
        )
        with patch.object(QMessageBox, "warning") as warning_mock:
            dialog.save_rows()
        warning_mock.assert_called_once()
        self.assertNotEqual(dialog.result(), dialog.DialogCode.Accepted)
        dialog.reject()

    def test_corrupt_draft_does_not_crash_or_delete_file(self):
        path = self.directory / "manual-video-drafts" / "project-a.json"
        path.parent.mkdir(parents=True)
        path.write_text("{bad", encoding="utf-8")
        with patch.object(QMessageBox, "warning"):
            dialog = self.dialog()
        self.assertTrue(path.is_file())
        dialog.reject()
        self.assertEqual(path.read_text(encoding="utf-8"), "{bad")

    def test_maximize_restore_and_f11_keep_values_and_column_ratio(self):
        dialog = self.dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Không mất dữ liệu")
        dialog.table.item(0, dialog.URL_COLUMN).setText("https://youtu.be/jNQXAC9IVRw")
        dialog.show()
        self.app.processEvents()
        total_width = dialog.table.columnWidth(dialog.TITLE_COLUMN) + dialog.table.columnWidth(dialog.URL_COLUMN)
        ratio = dialog.table.columnWidth(dialog.TITLE_COLUMN) / max(total_width, 1)
        self.assertAlmostEqual(ratio, 0.45, delta=0.1)
        dialog._toggle_maximized()
        self.assertTrue(dialog.isMaximized())
        dialog._toggle_maximized()
        self.assertFalse(dialog.isMaximized())
        QTest.keyClick(dialog, Qt.Key.Key_F11)
        self.assertEqual(dialog.table.item(0, dialog.TITLE_COLUMN).text(), "Không mất dữ liệu")
        self.assertEqual(dialog.table.item(0, dialog.URL_COLUMN).text(), "https://youtu.be/jNQXAC9IVRw")
        dialog.reject()


if __name__ == "__main__":
    unittest.main()

