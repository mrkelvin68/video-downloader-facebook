import http.server
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from app.core.exceptions import ImportBlockedError
from app.database.connection import Database
from app.services.download_queue_service import MAX_RETRIES, DownloadQueueService
from app.services.download_service import DownloadError, DownloadService
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.ui.download_worker import DownloadWorker


class DownloadQueueServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "projects.sqlite3"
        self.db = Database(self.db_path)
        projects = ProjectService(self.db)
        self.project_id = projects.create(
            "Queue project", "queue", str(self.root / "queue-project")
        )
        self.other_project_id = projects.create(
            "Other project", "other", str(self.root / "other-project")
        )
        text = "stt,ten_video,url\n" + "".join(
            f"{number},Video {number},https://www.facebook.com/reel/{number}\n"
            for number in range(1, 21)
        )
        videos = VideoService(self.db)
        videos.import_csv(self.project_id, text)
        videos.import_csv(self.other_project_id, text)
        self.video_ids = [
            row["id"] for row in self.db.list_videos(self.project_id)
        ]
        self.other_video_id = self.db.list_videos(self.other_project_id)[0]["id"]
        self.queue = DownloadQueueService(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def _active_codes(self):
        return [
            row["video_code"]
            for row in self.queue.list_jobs(include_finished=False)
            if row["status"] == "queued"
        ]

    def test_add_to_queue_is_persistent_ordered_and_deduplicated(self):
        created = self.queue.enqueue(
            self.project_id,
            [self.video_ids[4], self.video_ids[2], self.video_ids[0], self.video_ids[2]],
        )
        self.assertEqual(len(created), 3)
        self.assertEqual(
            self._active_codes(), ["queue-1", "queue-3", "queue-5"]
        )
        self.assertEqual(
            self.queue.enqueue(
                self.project_id,
                [self.video_ids[0], self.video_ids[2], self.video_ids[4]],
            ),
            [],
        )
        self.assertEqual(len(self.queue.list_jobs(include_finished=False)), 3)
        self.assertTrue(
            all(
                row["download_status"] == "waiting"
                for row in self.db.list_videos(self.project_id)[:5]
                if row["sequence_number"] in (1, 3, 5)
            )
        )

        reopened = Database(self.db_path)
        self.assertEqual(
            [
                row["video_code"]
                for row in DownloadQueueService(reopened).list_jobs(
                    include_finished=False
                )
            ],
            ["queue-1", "queue-3", "queue-5"],
        )
        reopened.close()

    def test_queue_twenty_videos_preserves_order_and_project_isolation(self):
        created = self.queue.enqueue(self.project_id, self.video_ids)
        self.assertEqual(len(created), 20)
        rows = self.queue.list_jobs(include_finished=False)
        self.assertEqual(
            [row["video_code"] for row in rows],
            [f"queue-{number}" for number in range(1, 21)],
        )
        self.assertTrue(all(row["project_id"] == self.project_id for row in rows))
        self.assertEqual(
            len(self.queue.list_jobs(include_finished=False)), 20
        )
        self.assertEqual(
            DownloadQueueService(self.db).video_job_info(self.other_project_id), {}
        )

    def test_run_now_when_queue_idle_prioritizes_selected_in_sequence_order(self):
        created = self.queue.enqueue(
            self.project_id, [self.video_ids[7], self.video_ids[3]], run_now=True
        )
        self.assertEqual(len(created), 2)
        self.assertEqual(self._active_codes(), ["queue-4", "queue-8"])
        self.assertEqual(self.queue.next_queued_job()["job_id"], created[0])

    def test_run_now_while_busy_preserves_current_and_moves_selection_to_head(self):
        self.queue.enqueue(
            self.project_id, [self.video_ids[0], self.video_ids[1], self.video_ids[2]]
        )
        current_job = self.queue.next_queued_job()["job_id"]
        self.assertEqual(self.queue.claim(current_job)["video_code"], "queue-1")

        created = self.queue.enqueue(
            self.project_id,
            [self.video_ids[7], self.video_ids[1], self.video_ids[5]],
            run_now=True,
        )
        self.assertEqual(len(created), 2)
        current = self.queue.get_job(current_job)
        self.assertEqual(current["status"], "downloading")
        next_job_id = self.queue.next_queued_job()["job_id"]
        next_code = self.db.connection.execute(
            """SELECT v.video_code FROM download_jobs j
               JOIN videos v ON v.id=j.video_id WHERE j.job_id=?""",
            (next_job_id,),
        ).fetchone()["video_code"]
        self.assertEqual(next_code, "queue-2")
        self.assertEqual(
            self._active_codes(), ["queue-2", "queue-6", "queue-8", "queue-3"]
        )

    def test_database_unique_index_blocks_duplicate_active_job(self):
        self.queue.enqueue(self.project_id, [self.video_ids[0]])
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connection:
                self.db.connection.execute(
                    """INSERT INTO download_jobs
                       (job_id, project_id, video_id, status, queue_order, created_at)
                       VALUES ('duplicate-job', ?, ?, 'queued', 99, '2026-01-01')""",
                    (self.project_id, self.video_ids[0]),
                )

    def test_completed_video_and_wrong_project_are_rejected(self):
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE videos SET download_status='completed' WHERE id=?",
                (self.video_ids[0],),
            )
        with self.assertRaisesRegex(ValueError, "đã tải"):
            self.queue.enqueue(self.project_id, [self.video_ids[0]])
        with self.assertRaisesRegex(ValueError, "không thuộc"):
            self.queue.enqueue(self.project_id, [self.other_video_id])

    def test_cancel_queued_job_leaves_video_file_untouched(self):
        job_id = self.queue.enqueue(self.project_id, [self.video_ids[0]])[0]
        video = self.db.list_videos(self.project_id)[0]
        video_folder = Path(self.root / "queue-project" / "existing-video")
        video_folder.mkdir()
        keep = video_folder / "keep.mp4"
        keep.write_bytes(b"keep")

        self.assertEqual(self.queue.cancel([job_id]), 1)
        self.assertEqual(self.queue.get_job(job_id)["status"], "cancelled")
        self.assertEqual(keep.read_bytes(), b"keep")
        self.assertEqual(
            self.db.list_videos(self.project_id)[0]["download_status"], "pending"
        )

    def test_import_replacement_is_blocked_while_jobs_are_queued(self):
        self.queue.enqueue(self.project_id, [self.video_ids[0]])
        self.assertFalse(self.db.project_videos_can_be_replaced(self.project_id))
        with self.assertRaises(ImportBlockedError):
            VideoService(self.db).import_csv(
                self.project_id,
                "stt,ten_video,url\n1,Replacement,https://www.facebook.com/reel/999\n",
                replace_confirmed=True,
            )
        self.assertEqual(len(self.db.list_videos(self.project_id)), 20)
        self.assertEqual(len(self.queue.list_jobs(include_finished=False)), 1)

    def test_prioritize_and_retry_failed_jobs(self):
        first, second, third = self.queue.enqueue(
            self.project_id, self.video_ids[:3]
        )
        self.queue.prioritize([third])
        self.assertEqual(self.queue.next_queued_job()["job_id"], third)
        self.assertEqual(self.queue.claim(third)["video_code"], "queue-3")
        self.queue.finish(third, status="failed", error_message="network error")
        retry = self.queue.retry([third])
        self.assertEqual(len(retry), 1)
        self.assertEqual(self.queue.get_job(retry[0])["status"], "queued")
        self.assertEqual(self.queue.next_queued_job()["job_id"], first)
        self.assertEqual(self.queue.get_job(first)["status"], "queued")
        self.assertEqual(self.queue.get_job(second)["status"], "queued")

    def test_retry_rejects_permanent_errors_and_stops_at_limit(self):
        permanent = self.queue.enqueue(self.project_id, [self.video_ids[0]])[0]
        self.queue.claim(permanent)
        self.queue.finish(
            permanent, status="failed", error_message="yt-dlp: Invalid URL"
        )
        self.assertEqual(self.queue.retry([permanent]), [])
        self.assertEqual(self.queue.get_job(permanent)["retry_count"], 0)

        current_job = self.queue.enqueue(self.project_id, [self.video_ids[1]])[0]
        original_job = current_job
        for retry_number in range(MAX_RETRIES + 1):
            self.queue.claim(current_job)
            self.queue.finish(
                current_job,
                status="failed",
                error_message="Network connection reset",
            )
            retry_jobs = self.queue.retry([current_job])
            if retry_number < MAX_RETRIES:
                self.assertEqual(len(retry_jobs), 1)
                current_job = retry_jobs[0]
                self.assertEqual(
                    self.queue.get_job(current_job)["retry_count"], retry_number + 1
                )
            else:
                self.assertEqual(retry_jobs, [])
                self.assertEqual(self.queue.retry([original_job]), [])

    def test_recovery_marks_worker_crash_interrupted_without_auto_retry(self):
        job_id = self.queue.enqueue(self.project_id, [self.video_ids[0]])[0]
        self.queue.claim(job_id)
        reopened = Database(self.db_path)
        recovered = DownloadQueueService(reopened).recover_interrupted()
        self.assertEqual(recovered, 1)
        jobs = DownloadQueueService(reopened)
        self.assertEqual(jobs.get_job(job_id)["status"], "interrupted")
        self.assertIsNone(jobs.next_queued_job())
        self.assertEqual(
            reopened.list_videos(self.project_id)[0]["download_status"], "failed"
        )
        reopened.close()

    def test_recovery_preserves_video_completed_before_job_commit(self):
        job_id = self.queue.enqueue(self.project_id, [self.video_ids[0]])[0]
        self.queue.claim(job_id)
        with self.db.connection:
            self.db.connection.execute(
                """UPDATE videos SET download_status='completed',
                   local_video_path='already-validated.mp4'
                   WHERE id=?""",
                (self.video_ids[0],),
            )
        self.assertEqual(self.queue.recover_interrupted(), 1)
        self.assertEqual(self.queue.get_job(job_id)["status"], "completed")
        self.assertEqual(
            self.db.list_videos(self.project_id)[0]["download_status"], "completed"
        )
        self.assertIsNone(self.queue.next_queued_job())

    def test_recovery_repairs_legacy_downloading_video_without_queue_job(self):
        with self.db.connection:
            self.db.connection.execute(
                "UPDATE videos SET download_status='downloading' WHERE id=?",
                (self.video_ids[0],),
            )
        self.assertEqual(self.queue.recover_interrupted(), 1)
        self.assertEqual(
            self.db.list_videos(self.project_id)[0]["download_status"], "failed"
        )

    def test_enqueue_rolls_back_all_rows_on_insert_failure(self):
        self.db.connection.execute(
            """INSERT INTO download_jobs
               (job_id, project_id, video_id, status, queue_order, created_at)
               VALUES ('fixed-job', ?, ?, 'queued', 0, '2026-01-01')""",
            (self.project_id, self.video_ids[10]),
        )
        self.db.connection.commit()
        with patch("app.services.download_queue_service.uuid.uuid4", return_value="fixed-job"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.queue.enqueue(
                    self.project_id,
                    [self.video_ids[5], self.video_ids[10]],
                )
        active = self.queue.list_jobs(include_finished=False)
        self.assertEqual(len(active), 1)
        self.assertEqual(
            self.db.list_videos(self.project_id)[5]["download_status"], "pending"
        )

    def test_integrated_priority_restart_sequential_worker_and_download_files(self):
        ffmpeg = DownloadService._find_ffmpeg()
        if not ffmpeg:
            self.skipTest("FFmpeg không có sẵn để chạy file integration test")
        fixture = self.root / "fixture.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=1:size=320x240:rate=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=1",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(fixture),
            ],
            check=True,
            capture_output=True,
        )

        self.queue.enqueue(self.project_id, self.video_ids[:5])
        self.queue.enqueue(
            self.project_id, [self.video_ids[6], self.video_ids[5]], run_now=True
        )
        expected_codes = [f"queue-{number}" for number in (6, 7, 1, 2, 3, 4, 5)]

        reopened = Database(self.db_path)
        queue_service = DownloadQueueService(reopened)
        self.assertEqual(
            [row["video_code"] for row in queue_service.list_jobs(include_finished=False)],
            expected_codes,
        )
        output_paths = []
        while (job := queue_service.next_queued_job()) is not None:
            job_row = queue_service.claim(job["job_id"])
            job_id = job_row["job_id"]

            def runner(command, **kwargs):
                if "-show_streams" in command:
                    return subprocess.run(command, **kwargs)
                template = command[command.index("-o") + 1]
                target = Path(template.replace("%(ext)s", "mp4"))
                shutil.copyfile(fixture, target)
                target.with_name(job_row["video_code"] + ".jpg").write_bytes(
                    b"thumbnail"
                )
                return SimpleNamespace(returncode=0, stderr="", stdout="")

            DownloadService(reopened, runner=runner).download(
                job_row["project_id"], job_row["video_id"]
            )
            queue_service.finish(job_id, status="completed")
            output_paths.append(
                Path(
                    reopened.connection.execute(
                        "SELECT local_video_path FROM videos WHERE id=?",
                        (job_row["video_id"],),
                    ).fetchone()["local_video_path"]
                )
            )

        self.assertEqual(len(output_paths), 7)
        self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path in output_paths))
        self.assertTrue(
            all((path.parent / "thumbnail.jpg").is_file() for path in output_paths)
        )
        self.assertTrue(
            all(job["status"] == "completed" for job in queue_service.list_jobs())
        )
        completed_id = queue_service.list_jobs()[0]["video_id"]
        with self.assertRaisesRegex(DownloadError, "đã tải"):
            DownloadService(reopened).download(self.project_id, completed_id)
        reopened.close()


class DownloadWorkerTests(unittest.TestCase):
    def test_worker_emits_real_progress_and_finishes_one_job(self):
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance() or QApplication([])
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            database = Database(root / "worker.sqlite3")
            project_id = ProjectService(database).create(
                "Worker", "worker", str(root / "worker-project")
            )
            VideoService(database).import_csv(
                project_id,
                "stt,ten_video,url\n1,Video,https://www.facebook.com/reel/1\n",
            )
            video = database.list_videos(project_id)[0]
            queue_service = DownloadQueueService(database)
            job_id = queue_service.enqueue(project_id, [video["id"]])[0]
            database.close()

            class FakeProcess:
                def __init__(self, *args, **kwargs):
                    self.stdout = iter(
                        (
                            "QUEUE_PROGRESS: 25.0%\t25\t100\t50.0\t2\n",
                            "QUEUE_PROGRESS:100.0%\t100\t100\tNone\t0\n",
                        )
                    )
                    self.returncode = 0

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    return self.returncode

            progress = []
            with patch("app.services.download_service.subprocess.Popen", FakeProcess):
                from app.services.download_service import DownloadService

                DownloadService._run_with_progress(
                    ["yt-dlp"], lambda *args: progress.append(args)
                )
            self.assertEqual(progress[0], (25.0, 25, 100, 50.0, 2))
            self.assertEqual(progress[1], (100.0, 100, 100, None, 0))

            class FakeDownloadService:
                def __init__(self, worker_db):
                    self.database = worker_db

                def download(self, worker_project_id, worker_video_id, *, progress_callback):
                    progress_callback(10.0, 10, 100, 50.0, 2)
                    progress_callback(20.0, 20, 100, 50.0, 2)
                    progress_callback(100.0, 100, 100, None, 0)
                    with self.database.connection:
                        self.database.connection.execute(
                            """UPDATE videos SET download_status='completed'
                               WHERE id=? AND project_id=?""",
                            (worker_video_id, worker_project_id),
                        )

            finished = []
            worker_progress = []
            worker = DownloadWorker(str(root / "worker.sqlite3"), job_id)
            worker.progress.connect(lambda *args: worker_progress.append(args))
            worker.jobFinished.connect(
                lambda active_job, success, message: finished.append(
                    (active_job, success, message)
                )
            )
            with patch("app.ui.download_worker.DownloadService", FakeDownloadService):
                worker.start()
                self.assertTrue(worker.wait(10000))
            application.processEvents()
            self.assertEqual(len(worker_progress), 3)
            self.assertEqual(finished, [(job_id, True, "")])
            verify = Database(root / "worker.sqlite3")
            self.assertEqual(
                DownloadQueueService(verify).get_job(job_id)["status"], "completed"
            )
            self.assertEqual(
                verify.list_videos(project_id)[0]["download_status"], "completed"
            )
            verify.close()
        finally:
            temp.cleanup()

    def test_worker_reports_progress_from_real_ytdlp_local_transfer(self):
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance() or QApplication([])
        ffmpeg = DownloadService._find_ffmpeg()
        if not ffmpeg:
            self.skipTest("FFmpeg không có sẵn để chạy progress integration test")
        temp = tempfile.TemporaryDirectory()
        server = None
        database = None
        try:
            root = Path(temp.name)
            source = root / "source.mp4"
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=duration=8:size=640x360:rate=24",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:duration=8",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-b:v",
                    "3M",
                    "-c:a",
                    "aac",
                    str(source),
                ],
                check=True,
                capture_output=True,
            )

            class QuietHandler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, format, *args):
                    return

            handler = lambda *args, **kwargs: QuietHandler(
                *args, directory=str(root), **kwargs
            )
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()

            database_path = root / "progress.sqlite3"
            database = Database(database_path)
            project_id = ProjectService(database).create(
                "Real yt-dlp progress", "live", str(root / "output")
            )
            VideoService(database).import_csv(
                project_id,
                "stt,ten_video,url\n1,Progress,https://www.facebook.com/reel/1\n",
            )
            video = database.list_videos(project_id)[0]
            local_url = (
                f"http://127.0.0.1:{server.server_port}/source.mp4"
            )
            with database.connection:
                database.connection.execute(
                    "UPDATE videos SET source_url=? WHERE id=?",
                    (local_url, video["id"]),
                )
            job_id = DownloadQueueService(database).enqueue(
                project_id, [video["id"]]
            )[0]
            database.close()
            database = None

            progress_events = []
            finished_events = []
            worker = DownloadWorker(str(database_path), job_id)
            worker.progress.connect(
                lambda *values: progress_events.append(values)
            )
            worker.jobFinished.connect(
                lambda *values: finished_events.append(values)
            )
            worker.start()
            self.assertTrue(worker.wait(60000))
            application.processEvents()

            verify = Database(database_path)
            job = DownloadQueueService(verify).get_job(job_id)
            video = verify.list_videos(project_id)[0]
            output = Path(video["local_video_path"])
            self.assertTrue(progress_events)
            self.assertGreater(progress_events[-1][1], 0)
            self.assertGreater(progress_events[-1][2], 0)
            self.assertEqual(finished_events, [(job_id, True, "")])
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["progress"], 100)
            self.assertEqual(video["download_status"], "completed")
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            verify.close()
        finally:
            if database is not None:
                database.close()
            if server is not None:
                server.shutdown()
                server.server_close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
