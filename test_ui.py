import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.connection import Database
from app.services.project_service import ProjectService
from app.services.download_queue_service import DownloadQueueService
from app.services.video_service import VideoService
from app.ui.dialogs.csv_preview_dialog import CsvPreviewDialog, CsvPreviewModel
from app.ui.main_window import MainWindow
from app.ui.pages.download_queue_page import DownloadQueuePage


class ProjectCsvUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "projects.sqlite3"
        self.database = Database(self.database_path)
        self.project_id = ProjectService(self.database).create(
            "Dự án UI", "ui", str(self.root / "videos")
        )
        self.csv_a = self.root / "a.csv"
        self.csv_a.write_text(
            "stt,ten_video,url\n"
            "3,CSV A-3,https://www.facebook.com/watch/?v=3\n"
            "1,CSV A-1,https://www.facebook.com/watch/?v=1\n",
            encoding="utf-8-sig",
        )
        self.csv_b = self.root / "b.csv"
        self.csv_b.write_text(
            "stt,ten_video,url\n"
            "2,CSV B,https://www.facebook.com/watch/?v=2\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.database.close()
        self.temp.cleanup()

    def open_project(self, window):
        project = next(
            item
            for item in ProjectService(self.database).list_projects()
            if item.uuid == self.project_id
        )
        window.show_detail(project)

    def test_preview_cancel_confirmation_restart_and_export(self):
        window = MainWindow(self.database)
        self.open_project(window)
        with patch("app.ui.main_window.CsvPreviewDialog.exec", return_value=0):
            window.load_csv(self.csv_a)
        self.assertEqual(self.database.list_videos(self.project_id), [])
        self.assertEqual(window.detail_page.video_table.model().rowCount(), 0)
        self.assertIsNone(window.pending_csv_text)

        with (
            patch(
                "app.ui.main_window.CsvPreviewDialog.exec",
                return_value=CsvPreviewDialog.DialogCode.Accepted,
            ),
            patch("app.ui.main_window.QMessageBox.information"),
        ):
            window.load_csv(self.csv_a)
        saved = self.database.list_videos(self.project_id)
        self.assertEqual(
            [(row["sequence_number"], row["video_code"]) for row in saved],
            [(1, "ui-1"), (3, "ui-3")],
        )
        window.close()
        self.database.close()

        self.database = Database(self.database_path)
        restarted = MainWindow(self.database)
        self.open_project(restarted)
        self.assertEqual(restarted.detail_page.video_table.model().rowCount(), 2)
        with patch("app.ui.main_window.CsvPreviewDialog.exec", return_value=0):
            restarted.load_csv(self.csv_b)
        self.assertIsNone(restarted.pending_csv_text)
        self.assertEqual(restarted.detail_page.video_table.model().rowCount(), 2)
        self.assertEqual(
            [row["title"] for row in self.database.list_videos(self.project_id)],
            ["CSV A-1", "CSV A-3"],
        )
        export_path = self.root / "export.csv"
        with patch(
            "app.ui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(export_path), "CSV (*.csv)"),
        ):
            restarted.export_csv()
        content = export_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("\ufeffstt,title,url"))
        self.assertLess(content.index("CSV A-1"), content.index("CSV A-3"))
        restarted.close()

    def test_invalid_csv_preview_cannot_be_confirmed_or_mutate_database(self):
        window = MainWindow(self.database)
        self.open_project(window)
        invalid = self.root / "invalid.csv"
        invalid.write_text(
            "stt,ten_video,url\n1,Bad,https://example.com/not-facebook\n",
            encoding="utf-8",
        )
        with patch("app.ui.main_window.CsvPreviewDialog.exec", return_value=0):
            window.load_csv(invalid)
        self.assertIsNone(window.pending_csv_text)
        with patch("app.ui.main_window.QMessageBox.information"):
            window.confirm_import()
        self.assertEqual(self.database.list_videos(self.project_id), [])
        window.close()

    def test_unreadable_file_clears_previous_unconfirmed_preview(self):
        window = MainWindow(self.database)
        self.open_project(window)
        with patch("app.ui.main_window.CsvPreviewDialog.exec", return_value=0):
            window.load_csv(self.csv_a)
        unreadable = self.root / "missing.csv"
        with patch("app.ui.main_window.QMessageBox.warning"):
            window.load_csv(unreadable)
        self.assertIsNone(window.pending_csv_text)
        with patch("app.ui.main_window.QMessageBox.information"):
            window.confirm_import()
        self.assertEqual(self.database.list_videos(self.project_id), [])
        window.close()

    def test_preview_model_is_read_only_and_shows_expected_codes_and_url_tooltip(self):
        from PySide6.QtCore import Qt

        long_url = "https://www.facebook.com/reel/" + "1" * 100
        rows = [{"stt": 6, "ten_video": "Tập thử", "url": long_url}]
        model = CsvPreviewModel(rows, "ep1-6")
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.columnCount(), 4)
        self.assertEqual(
            [
                model.headerData(index, Qt.Orientation.Horizontal)
                for index in range(4)
            ],
            ["STT", "Mã video dự kiến", "Tên video", "URL nguồn"],
        )
        self.assertEqual(model.data(model.index(0, 1)), "ep1-6-6")
        self.assertEqual(
            model.data(model.index(0, 3), Qt.ItemDataRole.ToolTipRole), long_url
        )
        self.assertNotEqual(model.data(model.index(0, 3)), long_url)
        self.assertFalse(
            model.flags(model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable
        )

    def test_project_video_status_tooltip_shows_download_error(self):
        from PySide6.QtCore import Qt

        VideoService(self.database).import_csv(
            self.project_id,
            "stt,title,url\n1,Reel,https://www.facebook.com/reel/1\n",
        )
        video = self.database.list_videos(self.project_id)[0]
        queue = DownloadQueueService(self.database)
        job_id = queue.enqueue(self.project_id, [video["id"]])[0]
        queue.claim(job_id)
        error = "[network] Connection reset by peer"
        queue.finish(job_id, status="failed", error_message=error)

        window = MainWindow(self.database)
        self.open_project(window)
        model = window.detail_page.video_table.model()
        status_index = model.index(0, 5)
        self.assertEqual(model.data(status_index), "Lỗi")
        self.assertEqual(
            model.data(status_index, Qt.ItemDataRole.ToolTipRole), error
        )
        window.close()

    def test_preview_dialog_sizes_one_twenty_and_one_thousand_rows(self):
        from PySide6.QtWidgets import QFrame, QPlainTextEdit, QPushButton

        for count in (1, 20, 1000):
            rows = [
                {
                    "stt": index + 1,
                    "ten_video": f"Video {index + 1}",
                    "url": f"https://www.facebook.com/reel/{index + 1}",
                }
                for index in range(count)
            ]
            dialog = CsvPreviewDialog("videos.csv", rows, [], "test-prefix")
            self.assertEqual(dialog.model.rowCount(), count)
            self.assertEqual(dialog.model.columnCount(), 4)
            self.assertEqual(dialog.model.data(dialog.model.index(count - 1, 1)), f"test-prefix-{count}")
            self.assertEqual(
                dialog.table.editTriggers(),
                dialog.table.EditTrigger.NoEditTriggers,
            )
            self.assertLessEqual(dialog.height(), int(dialog.screen().availableGeometry().height() * 0.82) + 1)
            buttons = dialog.findChildren(QPushButton)
            self.assertTrue(any(button.text() == "Hủy" for button in buttons))
            self.assertTrue(any(button.text() == "Xác nhận import" for button in buttons))
            dialog.show()
            self.application.processEvents()
            footer = dialog.findChild(QFrame, "dialogFooter")
            self.assertIsNotNone(footer)
            self.assertLess(
                dialog.table.geometry().bottom(), footer.geometry().top()
            )
            for button in buttons:
                if button.parentWidget() is footer:
                    self.assertTrue(footer.rect().contains(button.geometry()))
            dialog.reject()

        invalid = CsvPreviewDialog(
            "invalid.csv", [], ["Dòng 2: URL Facebook không hợp lệ"], "test-prefix"
        )
        confirm = next(
            button
            for button in invalid.findChildren(QPushButton)
            if button.text() == "Xác nhận import"
        )
        self.assertFalse(confirm.isEnabled())
        error_view = invalid.findChild(QPlainTextEdit, "csvPreviewErrors")
        self.assertEqual(error_view.toPlainText(), "Dòng 2: URL Facebook không hợp lệ")
        invalid.reject()

    def test_confirming_twenty_rows_imports_exact_codes_and_updates_table(self):
        window = MainWindow(self.database)
        self.open_project(window)
        csv_path = self.root / "twenty.csv"
        rows = ["stt,ten_video,url"]
        rows.extend(
            f"{index},Video {index},https://www.facebook.com/reel/{index}"
            for index in range(1, 21)
        )
        csv_path.write_text("\n".join(rows), encoding="utf-8")
        with (
            patch(
                "app.ui.main_window.CsvPreviewDialog.exec",
                return_value=CsvPreviewDialog.DialogCode.Accepted,
            ),
            patch("app.ui.main_window.QMessageBox.information"),
        ):
            window.load_csv(csv_path)
        saved = self.database.list_videos(self.project_id)
        self.assertEqual(len(saved), 20)
        self.assertEqual(
            [(row["sequence_number"], row["video_code"]) for row in saved],
            [(index, f"ui-{index}") for index in range(1, 21)],
        )
        self.assertEqual(window.detail_page.video_table.model().rowCount(), 20)
        window.close()

    def test_create_project_from_project_dialog_and_resize_main_window(self):
        window = MainWindow(self.database)
        output_dir = self.root / "created-from-ui"

        def accept_project_dialog(dialog):
            dialog.name.setText("Dự án từ UI")
            dialog.prefix.setText("ui-created")
            dialog.output_dir.setText(str(output_dir))
            dialog.validate_and_accept()
            return dialog.result()

        with patch(
            "app.ui.main_window.ProjectDialog.exec",
            new=accept_project_dialog,
        ):
            window.add_project()
        created = next(
            project
            for project in ProjectService(self.database).list_projects()
            if project.name == "Dự án từ UI"
        )
        self.assertTrue(output_dir.is_dir())
        self.assertEqual(created.prefix, "ui-created")
        self.assertEqual(window.pages.currentIndex(), 1)
        window.show()
        for width, height in ((1366, 768), (1920, 1080)):
            window.resize(width, height)
            self.application.processEvents()
            self.assertEqual(window.width(), width)
            self.assertGreater(window.height(), 700)
        screenshot = window.grab()
        self.assertFalse(screenshot.isNull())
        window.close()

    def test_queue_selection_actions_navigation_and_responsive_layout(self):
        from PySide6.QtCore import Qt

        rows = ["stt,ten_video,url"]
        rows.extend(
            f"{index},Video {index},https://www.facebook.com/reel/{index}"
            for index in range(1, 21)
        )
        VideoService(self.database).import_csv(self.project_id, "\n".join(rows))
        window = MainWindow(self.database)
        self.open_project(window)
        model = window.detail_page.video_table.video_model
        model.setData(
            model.index(0, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole
        )
        model.setData(
            model.index(2, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole
        )
        model.setData(
            model.index(4, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole
        )
        window.detail_page.enqueue_button.click()
        self.assertFalse(window.queue_running)
        queue = DownloadQueueService(self.database)
        self.assertEqual(
            [row["video_code"] for row in queue.list_jobs(include_finished=False)],
            ["ui-1", "ui-3", "ui-5"],
        )

        window.detail_page.video_table.clear_selection()
        model = window.detail_page.video_table.video_model
        model.setData(
            model.index(5, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole
        )
        model.setData(
            model.index(6, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole
        )
        with patch.object(window, "start_queue") as start_queue:
            window.detail_page.run_now_button.click()
            start_queue.assert_called_once()
        self.assertEqual(
            [
                row["video_code"]
                for row in queue.list_jobs(include_finished=False)
            ],
            ["ui-6", "ui-7", "ui-1", "ui-3", "ui-5"],
        )
        window.show()
        window.resize(1366, 768)
        self.application.processEvents()
        self.assertGreaterEqual(window.detail_page.video_table.height(), 190)
        window.sidebar.queue_button.click()
        self.assertEqual(window.pages.currentIndex(), 2)
        self.assertEqual(window.queue_page.table.queue_model.rowCount(), 5)

        window.show()
        for width, height in ((1366, 768), (1920, 1080)):
            window.resize(width, height)
            self.application.processEvents()
            self.assertEqual(window.width(), width)
            self.assertGreater(window.height(), 700)
            self.assertTrue(window.queue_page.table.isVisible())
            self.assertEqual(
                window.queue_page.table.horizontalScrollBar().maximum(), 0
            )
            self.assertLess(
                window.queue_page.table.geometry().top(),
                window.queue_page.table.geometry().bottom(),
            )
        self.assertTrue(window.grab().save(str(self.root / "queue-manager.png")))
        window.close()

    def test_unknown_download_size_uses_indeterminate_progress(self):
        from PySide6.QtCore import Qt

        VideoService(self.database).import_csv(
            self.project_id,
            "stt,ten_video,url\n1,Video,https://www.facebook.com/reel/1\n",
        )
        video = self.database.list_videos(self.project_id)[0]
        queue = DownloadQueueService(self.database)
        job_id = queue.enqueue(self.project_id, [video["id"]])[0]
        queue.claim(job_id)
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE download_jobs SET progress=0, total_bytes=NULL
                   WHERE job_id=?""",
                (job_id,),
            )
        page = DownloadQueuePage()
        page.set_jobs(queue.list_jobs())
        model = page.table.queue_model
        index = model.index(0, 4)
        self.assertEqual(model.data(index), "Đang tải…")
        self.assertEqual(
            model.data(index, Qt.ItemDataRole.UserRole),
            {"indeterminate": True, "value": 0, "text": "Đang tải…"},
        )
        self.assertIn("Hoàn tất 0", page.summary.text())
        self.assertIn("Lỗi 0", page.summary.text())
        page.close()

    def test_pause_after_current_then_resume_starts_next_job(self):
        class FakeSignal:
            def connect(self, callback):
                self.callback = callback

        class FakeWorker:
            started_jobs = []

            def __init__(self, database_path, job_id, parent):
                self.job_id = job_id
                self.progress = FakeSignal()
                self.jobFinished = FakeSignal()
                self.finished = FakeSignal()

            def start(self):
                self.started_jobs.append(self.job_id)

            def wait(self):
                return True

        rows = ["stt,ten_video,url"]
        rows.extend(
            f"{index},Video {index},https://www.facebook.com/reel/{index}"
            for index in range(1, 3)
        )
        VideoService(self.database).import_csv(self.project_id, "\n".join(rows))
        window = MainWindow(self.database)
        queue = DownloadQueueService(self.database)
        queue.enqueue(self.project_id, [row["id"] for row in self.database.list_videos(self.project_id)])
        with patch("app.ui.main_window.DownloadWorker", FakeWorker):
            window.start_queue()
            current_job = window.queue_job_id
            self.assertIsNotNone(current_job)
            window.pause_queue()
            self.assertTrue(window.pause_after_current)
            current = queue.claim(current_job)
            with self.database.connection:
                self.database.connection.execute(
                    """UPDATE videos SET download_status='completed'
                       WHERE id=? AND project_id=?""",
                    (current["video_id"], self.project_id),
                )
            queue.finish(current_job, status="completed")
            window._queue_job_finished(current_job, True, "")
            self.assertFalse(window.queue_running)
            self.assertIsNone(window.queue_worker)
            self.assertEqual(queue.next_queued_job()["job_id"] != current_job, True)

            window.start_queue()
            self.assertEqual(len(FakeWorker.started_jobs), 2)
            self.assertNotEqual(FakeWorker.started_jobs[0], FakeWorker.started_jobs[1])
            window.queue_worker = None
            window.queue_job_id = None
        window.close()

    def test_worker_database_failure_stops_queue_without_retry_loop(self):
        VideoService(self.database).import_csv(
            self.project_id,
            "stt,ten_video,url\n1,Video,https://www.facebook.com/reel/1\n",
        )
        video = self.database.list_videos(self.project_id)[0]
        job_id = DownloadQueueService(self.database).enqueue(
            self.project_id, [video["id"]]
        )[0]
        window = MainWindow(self.database)

        class FinishedWorker:
            def wait(self):
                return True

        window.queue_worker = FinishedWorker()
        window.queue_job_id = job_id
        window.queue_running = True
        with (
            patch.object(
                window.download_queue,
                "fail_unclaimed",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
            patch("app.ui.main_window.QTimer.singleShot") as schedule_next,
        ):
            window._queue_job_finished(job_id, False, "Could not open database")

        self.assertFalse(window.queue_running)
        self.assertEqual(window.download_queue.get_job(job_id)["status"], "queued")
        schedule_next.assert_not_called()
        self.assertIn("database is locked", window.queue_page.queue_status.text())
        window.close()


if __name__ == "__main__":
    unittest.main()
