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
        self.assertEqual(card.progress_label.text(), "Tải thất bại")
        self.assertEqual(card.stats_label.text(), "Video bị lỗi")

        card.setText("Tải thất bại: Video không thể kết nối")
        self.assertEqual(card.progress_label.text(), "Tải thất bại")

        card.setText("Hoàn tất: 2 video xong, 1 video lỗi.")
        self.assertEqual(card.progress_label.text(), "2 xong")
        self.assertEqual(card.stats_label.text(), "1 lỗi")

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

    def test_toast_does_not_falsely_complete_on_single_video_success(self):
        card = DownloadToastCard()
        card.set_idle("Sẵn sàng")
        card.setText("Video tải thành công.")
        # Must not switch to "Hoàn tất 100%" or completed state
        self.assertNotEqual(card.progress_label.text(), "Hoàn tất 100%")
        self.assertNotEqual(card.stats_label.text(), "Đã tải xong")

    def test_toast_card_handles_partial_error_ratio(self):
        card = DownloadToastCard()
        card.setText("Hoàn tất: 20/21 video (1 video lỗi).")
        self.assertEqual(card.progress_label.text(), "20 xong")
        self.assertEqual(card.stats_label.text(), "1 lỗi")

    def test_project_status_update_with_failed_and_completed_videos(self):
        from unittest.mock import MagicMock
        from app.models.video import Video

        class MockMainWindow:
            from app.ui.main_window import MainWindow
            _update_detail_queue_status_from_videos = MainWindow._update_detail_queue_status_from_videos

        mock_win = MockMainWindow()
        mock_win.detail_page = MagicMock()
        mock_win.detail_page.queue_status = DownloadToastCard()

        def make_video(vid, status):
            return Video(
                id=vid,
                project_id="p1",
                sequence_number=1,
                video_code=vid,
                title="title",
                source_url="https://fb.com/1",
                normalized_url="https://fb.com/1",
                download_status=status,
                local_video_path=None,
                thumbnail_path=None,
                facebook_uploaded=False,
                created_at="now",
                updated_at="now",
            )

        # 20 completed videos, 1 failed video
        videos = [make_video(f"v{i}", "completed") for i in range(20)]
        videos.append(make_video("v21", "failed"))

        mock_win._update_detail_queue_status_from_videos(videos)

        card = mock_win.detail_page.queue_status
        self.assertIn("20/21 video", card.title_label.full_text())
        self.assertIn("1 video lỗi", card.title_label.full_text())
        self.assertEqual(card.progress_label.text(), "20 xong")
        self.assertEqual(card.stats_label.text(), "1 lỗi")
        self.assertNotEqual(card.progress_label.text(), "Hoàn tất 100%")
        self.assertNotEqual(card.stats_label.text(), "Đã tải xong")

    def test_project_status_update_all_completed(self):
        from unittest.mock import MagicMock
        from app.models.video import Video

        class MockMainWindow:
            from app.ui.main_window import MainWindow
            _update_detail_queue_status_from_videos = MainWindow._update_detail_queue_status_from_videos

        mock_win = MockMainWindow()
        mock_win.detail_page = MagicMock()
        mock_win.detail_page.queue_status = DownloadToastCard()

        def make_video(vid, status):
            return Video(
                id=vid,
                project_id="p1",
                sequence_number=1,
                video_code=vid,
                title="title",
                source_url="https://fb.com/1",
                normalized_url="https://fb.com/1",
                download_status=status,
                local_video_path=None,
                thumbnail_path=None,
                facebook_uploaded=False,
                created_at="now",
                updated_at="now",
            )

        videos = [make_video(f"v{i}", "completed") for i in range(5)]
        mock_win._update_detail_queue_status_from_videos(videos)

        card = mock_win.detail_page.queue_status
        self.assertIn("Đã tải xong toàn bộ 5 video", card.title_label.full_text())
        self.assertEqual(card.progress_label.text(), "Hoàn tất 100%")
        self.assertEqual(card.stats_label.text(), "Đã tải xong")
        self.assertEqual(card.progress_bar.value(), 100)


if __name__ == "__main__":
    unittest.main()
