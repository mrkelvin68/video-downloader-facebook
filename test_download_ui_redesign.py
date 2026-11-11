import unittest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication

from app.ui.widgets.download_toast_card import DownloadToastCard
from app.ui.widgets.video_table import (
    VideoProgressDelegate,
    VideoStatusBadgeDelegate,
    VideoTable,
    VideoTableModel,
)


class DownloadUIRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_download_toast_card_set_text_progress(self):
        card = DownloadToastCard()
        card.setText("Đang tải gaubeo-review-11-11-2026-2: 42.0% · 1.2 MB/s · còn 30s")

        self.assertEqual(card.title_label.full_text(), "Đang tải: gaubeo-review-11-11-2026-2")
        self.assertEqual(card.progress_bar.value(), 42)
        self.assertEqual(card.progress_label.text(), "Đang tải: 42%")
        self.assertIn("1.2 MB/s", card.stats_label.text())
        self.assertIn("30s", card.stats_label.text())
        self.assertFalse(card.cancel_button.isHidden())
        self.assertEqual(card.text(), "Đang tải gaubeo-review-11-11-2026-2: 42.0% · 1.2 MB/s · còn 30s")

    def test_download_toast_card_states(self):
        card = DownloadToastCard()

        card.setText("Hàng đợi đã hoàn tất.")
        self.assertEqual(card.title_label.full_text(), "Hàng đợi đã hoàn tất.")
        self.assertEqual(card.progress_bar.value(), 100)
        self.assertEqual(card.progress_label.text(), "Hoàn tất 100%")

        card.setText("Hàng đợi đang tạm dừng.")
        self.assertEqual(card.title_label.full_text(), "Hàng đợi đang tạm dừng.")
        self.assertEqual(card.progress_label.text(), "Tạm dừng")

        card.setText("Job lỗi: timeout")
        self.assertEqual(card.title_label.full_text(), "Job lỗi: timeout")
        self.assertEqual(card.progress_label.text(), "Lỗi")

    def test_toast_cancel_signal(self):
        card = DownloadToastCard()
        emitted = []
        card.cancelRequested.connect(lambda: emitted.append(True))
        card.cancel_button.click()
        self.assertEqual(len(emitted), 1)

    def test_video_table_model_roles_and_delegates(self):
        videos = [
            {
                "id": "1",
                "sequence_number": 1,
                "video_code": "vid-1",
                "title": "Video Queued",
                "source_url": "https://fb.com/1",
                "queue_status": "queued",
                "download_status": "pending",
                "queue_progress": 0,
            },
            {
                "id": "2",
                "sequence_number": 2,
                "video_code": "vid-2",
                "title": "Video Downloading",
                "source_url": "https://fb.com/2",
                "queue_status": "downloading",
                "download_status": "pending",
                "queue_progress": 42.5,
            },
            {
                "id": "3",
                "sequence_number": 3,
                "video_code": "vid-3",
                "title": "Video Completed",
                "source_url": "https://fb.com/3",
                "queue_status": None,
                "download_status": "completed",
                "queue_progress": 100,
            },
        ]
        table = VideoTable()
        table.set_videos(videos)

        model = table.video_model
        self.assertEqual(model.rowCount(), 3)

        # Status text for column 5
        self.assertEqual(model.data(model.index(0, 5), Qt.ItemDataRole.DisplayRole), "Trong hàng đợi")
        self.assertEqual(model.data(model.index(1, 5), Qt.ItemDataRole.DisplayRole), "Đang tải")
        self.assertEqual(model.data(model.index(2, 5), Qt.ItemDataRole.DisplayRole), "Đã tải")

        # Progress text for column 6
        self.assertEqual(model.data(model.index(0, 6), Qt.ItemDataRole.DisplayRole), "◌ Chờ tải")
        self.assertEqual(model.data(model.index(1, 6), Qt.ItemDataRole.DisplayRole), "42%")
        self.assertEqual(model.data(model.index(2, 6), Qt.ItemDataRole.DisplayRole), "100%")

        # Test live progress update
        table.update_video_progress("1", 75.0, "downloading")
        self.assertEqual(model.data(model.index(0, 5), Qt.ItemDataRole.DisplayRole), "Đang tải")
        self.assertEqual(model.data(model.index(0, 6), Qt.ItemDataRole.DisplayRole), "75%")
        self.assertEqual(model.data(model.index(0, 6), Qt.ItemDataRole.UserRole), 75.0)

        # Verify delegates are set on columns 5 and 6
        self.assertIsInstance(table.itemDelegateForColumn(5), VideoStatusBadgeDelegate)
        self.assertIsInstance(table.itemDelegateForColumn(6), VideoProgressDelegate)


if __name__ == "__main__":
    unittest.main()
