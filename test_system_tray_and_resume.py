import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from app.database.connection import Database
from app.services.download_queue_service import DownloadQueueService
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.ui import main_window
from app.ui.main_window import MainWindow


class SystemTrayAndAutoResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "projects.sqlite3"
        self.database = Database(self.database_path)
        self.project_id = ProjectService(self.database).create(
            "Dự án Tray Test", "tray", str(self.root / "videos")
        )

    def tearDown(self):
        self.database.close()
        self.temp.cleanup()

    def test_system_tray_hide_on_close_and_restore(self):
        with patch.object(
            main_window.QSystemTrayIcon, "isSystemTrayAvailable", return_value=True
        ):
            window = MainWindow(self.database, auto_resume=False, enable_tray=True)
            window.show()
            self.assertTrue(window.isVisible())

            # Simulate user closing the window.
            event = QCloseEvent()
            window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertFalse(window.isVisible())

            # Restore from tray.
            window._restore_from_tray()
            self.assertTrue(window.isVisible())

            # Force exit.
            window._force_exit = True
            close_event = QCloseEvent()
            window.closeEvent(close_event)
            self.assertTrue(close_event.isAccepted())
            window.close()

    def test_auto_resume_retries_interrupted_jobs_and_triggers_queue(self):
        VideoService(self.database).import_csv(
            self.project_id,
            "stt,title,url\n1,Video 1,https://www.facebook.com/reel/1\n",
        )
        video = self.database.list_videos(self.project_id)[0]
        queue = DownloadQueueService(self.database)
        job_id = queue.enqueue(self.project_id, [video["id"]])[0]
        queue.claim(job_id)

        # Mark as downloading, then simulate abrupt crash/power loss
        queue.recover_interrupted()
        self.assertEqual(queue.get_job(job_id)["status"], "interrupted")

        with patch.object(MainWindow, "start_queue") as mock_start:
            window = MainWindow(self.database, auto_resume=True, enable_tray=False)
            # The interrupted job must be retried back to queued
            self.assertEqual(queue.get_job(job_id)["status"], "queued")
            # And _auto_resume_queue triggers start_queue
            window._auto_resume_queue()
            mock_start.assert_called_once()
            window.close()

    def test_main_exits_when_the_main_window_closes(self):
        application = MagicMock()
        application.exec.return_value = 0
        database = MagicMock()
        window = MagicMock()

        with (
            patch.object(main_window, "acquire_single_instance", return_value=True),
            patch.object(main_window, "QApplication", return_value=application),
            patch.object(main_window, "Database", return_value=database),
            patch.object(main_window, "MainWindow", return_value=window) as main_window_cls,
        ):
            self.assertEqual(main_window.main(), 0)

        application.setQuitOnLastWindowClosed.assert_called_once_with(True)
        main_window_cls.assert_called_once_with(database, auto_resume=True, enable_tray=False)
        window.show.assert_called_once_with()
        database.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
