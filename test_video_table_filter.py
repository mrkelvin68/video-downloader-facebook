import unittest

from PySide6.QtWidgets import QApplication

from app.ui.widgets.video_table import VideoTableModel


class VideoTableFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_downloaded_and_not_downloaded_filters(self):
        model = VideoTableModel([
            {"id": "1", "title": "Done", "download_status": "completed"},
            {"id": "2", "title": "Waiting", "download_status": "pending"},
            {"id": "3", "title": "Failed", "download_status": "failed"},
        ])
        model.set_status_filter("Đã tải")
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.data(model.index(0, 3)), "Done")
        model.set_status_filter("Chưa tải")
        self.assertEqual(model.rowCount(), 2)
        model.set_status_filter("Tất cả")
        self.assertEqual(model.rowCount(), 3)


if __name__ == "__main__":
    unittest.main()
