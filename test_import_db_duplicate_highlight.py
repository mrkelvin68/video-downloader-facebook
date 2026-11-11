import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QApplication, QMessageBox

from app.database.connection import Database
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.ui.dialogs.manual_video_import_dialog import ManualVideoImportDialog


class ImportDbDuplicateHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_db_dup_"))
        self.db = Database(self.temp_dir / "test.sqlite3")
        self.project_service = ProjectService(self.db)
        self.video_service = VideoService(self.db)
        self.project_id = self.project_service.create(
            "Test Project", "TP", str(self.temp_dir / "videos")
        )
        # Seed an existing video into the database:
        # URL: https://www.youtube.com/watch?v=xD-iIGhjGMU
        self.existing_url = "https://www.youtube.com/watch?v=xD-iIGhjGMU"
        self.existing_url_2 = "https://www.facebook.com/reel/123456789"
        self.video_service.import_csv(
            self.project_id,
            f"stt,title,url\n1,Existing Video 1,{self.existing_url}\n2,Existing Video 2,{self.existing_url_2}",
            append=True,
        )

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dialog(self) -> ManualVideoImportDialog:
        return ManualVideoImportDialog(
            project_id=self.project_id,
            draft_directory=self.temp_dir / "drafts",
            video_service=self.video_service,
        )

    def test_case_1_single_db_duplicate_highlighted(self):
        """Case 1: 1 dòng trùng DB -> highlight đúng dòng với màu #5A2630."""
        dialog = self._create_dialog()
        # Row 0: DB duplicate URL
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Duplicate Video")
        dialog.table.item(0, dialog.URL_COLUMN).setText(self.existing_url)
        # Row 1: Valid new URL
        dialog.add_row()
        dialog.table.item(1, dialog.TITLE_COLUMN).setText("Valid Video")
        dialog.table.item(1, dialog.URL_COLUMN).setText("https://youtu.be/jNQXAC9IVRw")

        with patch.object(QMessageBox, "warning") as mock_warn:
            dialog.save_rows()

        mock_warn.assert_called_once()
        self.assertNotEqual(dialog.result(), dialog.DialogCode.Accepted)

        # Row 0 must be highlighted in #5A2630 across all columns
        self.assertEqual(
            dialog.table.item(0, dialog.TITLE_COLUMN).background().color().name().lower(),
            "#5a2630",
        )
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )

        # Row 1 must have default background
        self.assertEqual(
            dialog.table.item(1, dialog.TITLE_COLUMN).background().style(),
            Qt.BrushStyle.NoBrush,
        )
        self.assertEqual(
            dialog.table.item(1, dialog.URL_COLUMN).background().style(),
            Qt.BrushStyle.NoBrush,
        )
        dialog.reject()

    def test_case_2_multiple_db_duplicates_all_highlighted(self):
        """Case 2: Nhiều dòng trùng DB -> highlight toàn bộ các dòng liên quan."""
        dialog = self._create_dialog()
        # Row 0: DB duplicate 1
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Dup 1")
        dialog.table.item(0, dialog.URL_COLUMN).setText(self.existing_url)
        # Row 1: Valid
        dialog.add_row()
        dialog.table.item(1, dialog.TITLE_COLUMN).setText("Valid")
        dialog.table.item(1, dialog.URL_COLUMN).setText("https://youtu.be/jNQXAC9IVRw")
        # Row 2: DB duplicate 2
        dialog.add_row()
        dialog.table.item(2, dialog.TITLE_COLUMN).setText("Dup 2")
        dialog.table.item(2, dialog.URL_COLUMN).setText(self.existing_url_2)

        with patch.object(QMessageBox, "warning"):
            dialog.save_rows()

        # Rows 0 and 2 must be highlighted
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )
        self.assertEqual(
            dialog.table.item(2, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )
        # Row 1 must NOT be highlighted
        self.assertEqual(
            dialog.table.item(1, dialog.URL_COLUMN).background().style(),
            Qt.BrushStyle.NoBrush,
        )
        dialog.reject()

    def test_case_3_close_message_box_keeps_highlight(self):
        """Case 3 & Regression: Đóng QMessageBox/popup lỗi -> highlight vẫn giữ nguyên."""
        dialog = self._create_dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Dup Video")
        dialog.table.item(0, dialog.URL_COLUMN).setText(self.existing_url)

        # Simulate user clicking OK on warning dialog
        with patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok):
            dialog.save_rows()

        # Dialog is still alive and not accepted
        self.assertNotEqual(dialog.result(), dialog.DialogCode.Accepted)
        # Background remains #5a2630
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )
        self.assertEqual(
            dialog.table.item(0, dialog.TITLE_COLUMN).background().color().name().lower(),
            "#5a2630",
        )
        dialog.reject()

    def test_case_4_editing_url_clears_highlight(self):
        """Case 4: Sửa URL trùng thành URL mới -> highlight biến mất."""
        dialog = self._create_dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Dup Video")
        dialog.table.item(0, dialog.URL_COLUMN).setText(self.existing_url)

        with patch.object(QMessageBox, "warning"):
            dialog.save_rows()

        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )

        # User edits URL to a non-duplicate URL
        dialog.table.item(0, dialog.URL_COLUMN).setText("https://youtu.be/jNQXAC9IVRw")

        # Highlight must be cleared
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().style(),
            Qt.BrushStyle.NoBrush,
        )
        self.assertEqual(
            dialog.table.item(0, dialog.TITLE_COLUMN).background().style(),
            Qt.BrushStyle.NoBrush,
        )
        dialog.reject()

    def test_case_5_saving_again_with_db_duplicate_rehighlights(self):
        """Case 5: Sửa nhưng vẫn trùng DB và bấm Save lại -> highlight xuất hiện lại."""
        dialog = self._create_dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Dup Video")
        dialog.table.item(0, dialog.URL_COLUMN).setText(self.existing_url)

        with patch.object(QMessageBox, "warning"):
            dialog.save_rows()
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )

        # User changes URL to canonical equivalent or another DB duplicate
        dialog.table.item(0, dialog.URL_COLUMN).setText(self.existing_url_2)
        # When typing, highlight cleared
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().style(),
            Qt.BrushStyle.NoBrush,
        )

        # User saves again
        with patch.object(QMessageBox, "warning"):
            dialog.save_rows()

        # Re-highlighted
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )
        dialog.reject()

    def test_case_6_delete_row_does_not_shift_highlight_incorrectly(self):
        """Case 6: Xóa một dòng -> không làm lệch vị trí highlight."""
        dialog = self._create_dialog()
        # Row 0: Valid
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Row 0 Valid")
        dialog.table.item(0, dialog.URL_COLUMN).setText("https://youtu.be/jNQXAC9IVRw")
        # Row 1: DB Duplicate
        dialog.add_row()
        dialog.table.item(1, dialog.TITLE_COLUMN).setText("Row 1 Dup")
        dialog.table.item(1, dialog.URL_COLUMN).setText(self.existing_url)
        # Row 2: Valid
        dialog.add_row()
        dialog.table.item(2, dialog.TITLE_COLUMN).setText("Row 2 Valid")
        dialog.table.item(2, dialog.URL_COLUMN).setText("https://www.instagram.com/reel/ABC123/")

        with patch.object(QMessageBox, "warning"):
            dialog.save_rows()

        self.assertEqual(
            dialog.table.item(1, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )

        # Delete Row 0
        dialog.table.selectRow(0)
        dialog.delete_rows()

        # Now the duplicate row is shifted to Row 0, and MUST still be highlighted!
        self.assertEqual(
            dialog.table.item(0, dialog.TITLE_COLUMN).text(),
            "Row 1 Dup",
        )
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )
        # Row 1 (previously Row 2) must NOT be highlighted
        self.assertEqual(
            dialog.table.item(1, dialog.TITLE_COLUMN).text(),
            "Row 2 Valid",
        )
        self.assertEqual(
            dialog.table.item(1, dialog.URL_COLUMN).background().style(),
            Qt.BrushStyle.NoBrush,
        )
        dialog.reject()

    def test_case_7_invalid_url_format_not_highlighted(self):
        """Case 7: URL sai định dạng nhưng không trùng DB -> KHÔNG highlight #5A2630."""
        dialog = self._create_dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Bad URL")
        dialog.table.item(0, dialog.URL_COLUMN).setText("https://invalid-domain.xyz/video/123")

        with patch.object(QMessageBox, "warning"):
            dialog.save_rows()

        # Background must remain default
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().style(),
            Qt.BrushStyle.NoBrush,
        )
        self.assertEqual(
            dialog.table.item(0, dialog.TITLE_COLUMN).background().style(),
            Qt.BrushStyle.NoBrush,
        )
        dialog.reject()

    def test_case_8_in_import_duplicate_not_highlighted_with_db_color(self):
        """Case 8: Trùng trong danh sách nhập nhưng chưa có trong DB -> KHÔNG dùng màu #5A2630."""
        dialog = self._create_dialog()
        new_url = "https://youtu.be/jNQXAC9IVRw"
        # Row 0
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("First in list")
        dialog.table.item(0, dialog.URL_COLUMN).setText(new_url)
        # Row 1 duplicate of row 0
        dialog.add_row()
        dialog.table.item(1, dialog.TITLE_COLUMN).setText("Second in list")
        dialog.table.item(1, dialog.URL_COLUMN).setText(new_url)

        with patch.object(QMessageBox, "warning"):
            dialog.save_rows()

        # Neither row should have #5A2630 background
        self.assertNotEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )
        self.assertNotEqual(
            dialog.table.item(1, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )
        dialog.reject()

    def test_case_9_save_success_saves_to_database(self):
        """Case 9: Save thành công -> lưu vào database."""
        dialog = self._create_dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Brand New Video")
        dialog.table.item(0, dialog.URL_COLUMN).setText("https://youtu.be/jNQXAC9IVRw")

        dialog.save_rows()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)

        imported = self.video_service.import_csv(
            self.project_id, dialog.csv_text, append=True
        )
        self.assertEqual(imported, 1)
        dialog.reject()

    def test_case_10_draft_autosave_preserves_data(self):
        """Case 10: Autosave draft -> không mất dữ liệu."""
        dialog = self._create_dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Draft Title")
        dialog.table.item(0, dialog.URL_COLUMN).setText("https://youtu.be/jNQXAC9IVRw")
        dialog.save_draft()

        self.assertTrue(dialog._draft_path.is_file())
        restored = dialog.restore_draft()
        self.assertIsNotNone(restored)
        self.assertEqual(restored[0]["ten_video"], "Draft Title")
        self.assertEqual(restored[0]["url"], "https://youtu.be/jNQXAC9IVRw")
        dialog.reject()

    def test_case_11_selection_and_viewport_preserve_highlight(self):
        """Case 11: Chọn/bỏ chọn dòng và cuộn bảng -> giữ nguyên background."""
        dialog = self._create_dialog()
        dialog.table.item(0, dialog.TITLE_COLUMN).setText("Dup Video")
        dialog.table.item(0, dialog.URL_COLUMN).setText(self.existing_url)

        with patch.object(QMessageBox, "warning"):
            dialog.save_rows()

        # Select row 0
        dialog.table.selectRow(0)
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )

        # Deselect
        dialog.table.clearSelection()
        self.assertEqual(
            dialog.table.item(0, dialog.URL_COLUMN).background().color().name().lower(),
            "#5a2630",
        )
        dialog.reject()


if __name__ == "__main__":
    unittest.main()
