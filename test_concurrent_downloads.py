import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from app.database.connection import Database
from app.services.download_queue_service import DownloadQueueService
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.ui.download_worker import DownloadWorker
from app.ui.main_window import MainWindow


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class FakeWorker:
    instances = []

    def __init__(self, database_path, job_id, parent):
        self.job_id = job_id
        self.progress = FakeSignal()
        self.jobFinished = FakeSignal()
        self.finished = FakeSignal()
        self.started = False
        self.cancelled = False
        FakeWorker.instances.append(self)

    def start(self):
        assert not self.started, "worker started twice"
        self.started = True

    def wait(self, *_args):
        return True

    def request_cancel(self):
        self.cancelled = True


class FakeSettings:
    store = {}

    def __init__(self, *_args):
        pass

    def value(self, key, default=None):
        return self.store.get(key, default)

    def setValue(self, key, value):
        self.store[key] = value


class ConcurrentQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        FakeWorker.instances = []
        FakeSettings.store = {}
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = Database(self.root / "projects.sqlite3")
        self.project_id = ProjectService(self.database).create(
            "Dự án song song", "par", str(self.root / "videos")
        )
        self.patches = [
            patch("app.ui.main_window.DownloadWorker", FakeWorker),
            patch("app.ui.main_window.QSettings", FakeSettings),
        ]
        for item in self.patches:
            item.start()
        self.queue = DownloadQueueService(self.database)

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.database.close()
        self.temp.cleanup()

    def enqueue(self, count):
        rows = "\n".join(
            f"{i},Video {i},https://www.facebook.com/reel/{i}" for i in range(1, count + 1)
        )
        VideoService(self.database).import_csv(
            self.project_id, "stt,ten_video,url\n" + rows + "\n"
        )
        ids = [v["id"] for v in self.database.list_videos(self.project_id)]
        return self.queue.enqueue(self.project_id, ids)

    def window(self, slots):
        window = MainWindow(self.database)
        window.queue_concurrency = slots
        return window

    def complete(self, window, job_id, ok=True):
        self.queue.claim(job_id)
        self.queue.finish(job_id, status="completed" if ok else "failed")
        window._queue_job_finished(job_id, ok, "" if ok else "boom")
        self.application.processEvents()

    def active_ids(self, window):
        return sorted(window.queue_workers)

    def test_slot_counts_with_ten_jobs(self):
        for slots in (1, 2, 3, 5):
            with self.subTest(slots=slots):
                FakeWorker.instances = []
                temp_db = Database(self.root / f"s{slots}.sqlite3")
                project = ProjectService(temp_db).create("P", f"p{slots}", str(self.root / f"v{slots}"))
                rows = "\n".join(f"{i},V{i},https://www.facebook.com/reel/{i}" for i in range(1, 11))
                VideoService(temp_db).import_csv(project, "stt,ten_video,url\n" + rows + "\n")
                DownloadQueueService(temp_db).enqueue(project, [v["id"] for v in temp_db.list_videos(project)])
                window = MainWindow(temp_db)
                window.queue_concurrency = slots
                window.start_queue()
                self.assertEqual(len(window.queue_workers), slots)
                self.assertEqual(len(FakeWorker.instances), slots)
                self.assertEqual(len({w.job_id for w in FakeWorker.instances}), slots)
                self.assertEqual(DownloadQueueService(temp_db).count_jobs(("queued",)), 10)
                temp_db.close()

    def test_acceptance_ten_jobs_three_slots_refill_and_priority(self):
        jobs = self.enqueue(10)
        window = self.window(3)
        window.start_queue()
        self.assertEqual(self.active_ids(window), sorted(jobs[:3]))
        self.complete(window, jobs[0])
        self.assertEqual(len(window.queue_workers), 3)
        self.assertIn(jobs[3], window.queue_workers)
        self.assertEqual(len(FakeWorker.instances), 4)
        self.assertEqual(len({w.job_id for w in FakeWorker.instances}), 4)

    def test_priority_scheduling(self):
        jobs = self.enqueue(5)
        self.queue.prioritize([jobs[4]])
        window = self.window(2)
        window.start_queue()
        self.assertIn(jobs[4], window.queue_workers)
        self.assertIn(jobs[0], window.queue_workers)

    def test_failed_worker_does_not_stop_others(self):
        jobs = self.enqueue(5)
        window = self.window(3)
        window.start_queue()
        self.complete(window, jobs[1], ok=False)
        self.assertEqual(len(window.queue_workers), 3)
        self.assertIn(jobs[0], window.queue_workers)
        self.assertIn(jobs[2], window.queue_workers)
        self.assertIn(jobs[3], window.queue_workers)
        self.assertEqual(self.queue.get_job(jobs[1])["status"], "failed")

    def test_pause_after_active_then_resume(self):
        jobs = self.enqueue(6)
        window = self.window(3)
        window.start_queue()
        window.pause_queue()
        self.assertTrue(window.pause_after_current)
        self.complete(window, jobs[0])
        self.assertEqual(len(window.queue_workers), 2)
        self.assertEqual(len(FakeWorker.instances), 3)
        self.assertTrue(window.queue_running or window.queue_workers)
        self.complete(window, jobs[1])
        self.complete(window, jobs[2])
        self.assertFalse(window.queue_running)
        self.assertFalse(window.pause_after_current)
        self.assertEqual(len(window.queue_workers), 0)
        self.assertEqual(self.queue.count_jobs(("queued",)), 3)
        window.start_queue()
        self.assertEqual(len(window.queue_workers), 3)

    def test_cancel_single_active_job_only_cancels_that_worker(self):
        jobs = self.enqueue(4)
        window = self.window(3)
        window.start_queue()
        for item in FakeWorker.instances:
            self.queue.claim(item.job_id)
        window.pending_queue_delete = ([jobs[0]], "job")
        window._pending_wait_ids = {jobs[0]}
        window.queue_workers[jobs[0]].request_cancel()
        cancelled = [w.job_id for w in FakeWorker.instances if w.cancelled]
        self.assertEqual(cancelled, [jobs[0]])

    def test_no_duplicate_job_or_video(self):
        jobs = self.enqueue(3)
        window = self.window(5)
        window.start_queue()
        window._start_next_queue_job()
        window._start_next_queue_job()
        self.assertEqual(len(FakeWorker.instances), 3)
        video_id = self.queue.get_job(jobs[0])["video_id"]
        self.assertIsNone(
            self.queue.next_queued_job(exclude_job_ids=tuple(jobs))
        )
        self.assertNotEqual(
            (self.queue.next_queued_job(exclude_video_ids=(video_id,)) or {"job_id": None})["job_id"],
            jobs[0],
        )

    def test_duplicate_completion_event_ignored(self):
        jobs = self.enqueue(4)
        window = self.window(2)
        window.start_queue()
        self.complete(window, jobs[0])
        count = len(FakeWorker.instances)
        window._queue_job_finished(jobs[0], True, "")
        self.application.processEvents()
        self.assertEqual(len(FakeWorker.instances), count)

    def test_restart_restores_pending_jobs(self):
        jobs = self.enqueue(5)
        window = self.window(2)
        window.start_queue()
        self.assertEqual(len(window.queue_workers), 2)
        # Simulate crash: jobs 0,1 claimed ('downloading'), app restarts.
        for job_id in jobs[:2]:
            self.queue.claim(job_id)
        FakeWorker.instances = []
        restarted = self.window(2)
        self.assertEqual(restarted.queue_workers, {})
        self.assertNotIn("downloading", [j["status"] for j in self.queue.list_jobs()])
        self.assertNotIn("completed", [j["status"] for j in self.queue.list_jobs()])
        restarted.start_queue()
        self.assertEqual(len(restarted.queue_workers), 2)

    def test_large_file_progress_and_aggregate_speed(self):
        jobs = self.enqueue(3)
        window = self.window(3)
        window.start_queue()
        big = 5_128_693_462
        window._queue_progress(jobs[0], 50.0, big, big * 2, 1000.0, 5)
        window._queue_progress(jobs[1], 10.0, 10, 100, 500.0, 5)
        window._queue_progress(jobs[2], 20.0, 20, 100, None, None)
        self.assertEqual(window._queue_speeds[jobs[0]], 1000.0)
        self.assertEqual(sum(window._queue_speeds[j] for j in jobs), 1500.0)
        model = window.queue_page.table.queue_model
        row = model.row_for_job(jobs[0])
        self.assertEqual(model._jobs[row]["downloaded_bytes"], big)
        self.assertIn("Tốc độ tổng", window.queue_page.stats_label.text())
        self.assertIn("Đang tải: 3", window.queue_page.stats_label.text())

    def test_project_video_table_refreshes_during_download(self):
        job_id = self.enqueue(1)[0]
        window = self.window(1)
        project = next(
            item for item in ProjectService(self.database).list_projects()
            if item.uuid == self.project_id
        )
        window.show_detail(project)
        window.start_queue()
        self.queue.claim(job_id)
        self.queue.update_progress(
            job_id, progress=50.0, downloaded_bytes=50, total_bytes=100,
            speed=10.0, eta_seconds=5,
        )
        window._queue_progress(job_id, 50.0, 50, 100, 10.0, 5)
        model = window.detail_page.video_table.video_model
        self.assertEqual(model.data(model.index(0, 5)), "Đang tải")
        self.assertEqual(model.data(model.index(0, 6)), "50%")

    def test_progress_ui_updates_are_throttled(self):
        jobs = self.enqueue(1)
        window = self.window(1)
        window.start_queue()
        calls = []
        model = window.queue_page.table.queue_model
        original = model.update_progress
        model.update_progress = lambda *a: (calls.append(a), original(*a))
        for i in range(50):
            window._queue_progress(jobs[0], float(i), i, 100, 1.0, 1)
        self.assertLess(len(calls), 5)
        window._queue_progress(jobs[0], 100.0, 100, 100, 1.0, 0)
        self.assertEqual(calls[-1][1], 100.0)

    def test_concurrency_persisted_and_default(self):
        self.assertEqual(MainWindow._load_queue_concurrency(), 2)
        window = self.window(2)
        window.set_queue_concurrency(4)
        self.assertEqual(FakeSettings.store["downloads/concurrency"], 4)
        self.assertEqual(MainWindow._load_queue_concurrency(), 4)
        window.set_queue_concurrency(99)
        self.assertEqual(window.queue_concurrency, 5)
        window.set_queue_concurrency(0)
        self.assertEqual(window.queue_concurrency, 1)

    def test_raising_concurrency_fills_slots_while_running(self):
        self.enqueue(5)
        window = self.window(1)
        window.start_queue()
        window.set_queue_concurrency(3)
        self.assertEqual(len(window.queue_workers), 3)


class RealThreadConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_real_workers_run_concurrently_with_thread_owned_connections(self):
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            path = root / "real.sqlite3"
            database = Database(path)
            project = ProjectService(database).create("R", "r", str(root / "v"))
            rows = "\n".join(f"{i},V{i},https://www.facebook.com/reel/{i}" for i in range(1, 7))
            VideoService(database).import_csv(project, "stt,ten_video,url\n" + rows + "\n")
            jobs = DownloadQueueService(database).enqueue(
                project, [v["id"] for v in database.list_videos(project)]
            )
            database.close()

            lock = threading.Lock()
            state = {"now": 0, "peak": 0, "output": set(), "dupes": 0}

            class SlowService:
                def __init__(self, worker_db):
                    self.database = worker_db

                def download(self, project_id, video_id, *, progress_callback, cancel_event=None):
                    with lock:
                        state["now"] += 1
                        state["peak"] = max(state["peak"], state["now"])
                        if video_id in state["output"]:
                            state["dupes"] += 1
                        state["output"].add(video_id)
                    for step in range(3):
                        time.sleep(0.05)
                        progress_callback(step * 33.0, 3_000_000_000 * (step + 1), None, 1.0, None)
                    with self.database.connection:
                        self.database.connection.execute(
                            "UPDATE videos SET download_status='completed' WHERE id=? AND project_id=?",
                            (video_id, project_id),
                        )
                    with lock:
                        state["now"] -= 1

            workers = [DownloadWorker(str(path), job_id) for job_id in jobs[:3]]
            seen = []
            for worker in workers:
                worker.progress.connect(lambda *a: seen.append(a))
            with patch("app.ui.download_worker.DownloadService", SlowService):
                for worker in workers:
                    worker.start()
                for worker in workers:
                    self.assertTrue(worker.wait(15000))
            self.application.processEvents()
            self.assertEqual(state["peak"], 3)
            self.assertEqual(state["dupes"], 0)
            self.assertTrue(any(a[2] > 2**31 for a in seen))
            verify = Database(path)
            statuses = [DownloadQueueService(verify).get_job(j)["status"] for j in jobs[:3]]
            self.assertEqual(statuses, ["completed"] * 3)
            self.assertEqual(DownloadQueueService(verify).count_jobs(("queued",)), 3)
            verify.close()
            for worker in workers:
                self.assertFalse(worker.isRunning())
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()

