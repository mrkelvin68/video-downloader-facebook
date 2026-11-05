"""Runs one persisted queue job on a thread-owned SQLite connection."""
from __future__ import annotations

import time
import threading

from PySide6.QtCore import QThread, Signal

from app.database.connection import Database
from app.services.download_queue_service import MAX_RETRIES, DownloadQueueService
from app.services.download_service import (
    DownloadCancelled,
    DownloadError,
    DownloadService,
)


class DownloadWorker(QThread):
    progress = Signal(str, float, object, object, object, object)
    jobFinished = Signal(str, bool, str)

    def __init__(self, database_path: str, job_id: str, parent=None) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.job_id = job_id
        self.cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        database = None
        claimed = False
        error_message = ""
        try:
            database = Database(self.database_path)
            jobs = DownloadQueueService(database)
            job = jobs.claim(self.job_id)
            if job is None:
                raise RuntimeError("Job không còn ở trạng thái chờ.")
            claimed = True

            last_persisted_at = 0.0

            def report_progress(
                percent: float,
                downloaded: int,
                total: int | None,
                speed: float | None,
                eta: int | None,
            ) -> None:
                nonlocal last_persisted_at
                now = time.monotonic()
                if now - last_persisted_at >= 2 or percent >= 100:
                    jobs.update_progress(
                        self.job_id,
                        progress=percent,
                        downloaded_bytes=downloaded,
                        total_bytes=total,
                        speed=speed,
                        eta_seconds=eta,
                    )
                    last_persisted_at = now
                self.progress.emit(
                    self.job_id, percent, downloaded, total, speed, eta
                )

            retry_count = job["retry_count"]
            downloader = DownloadService(database)
            while True:
                try:
                    downloader.download(
                        job["project_id"],
                        job["video_id"],
                        progress_callback=report_progress,
                        cancel_event=self.cancel_event,
                    )
                    break
                except DownloadError as exc:
                    if not exc.retryable or retry_count >= MAX_RETRIES:
                        raise
                    retry_count = jobs.record_retry(self.job_id, str(exc))
                    if retry_count is None:
                        raise
                    delay = 2 ** (retry_count - 1)
                    time.sleep(delay)
                    if self.cancel_event.is_set():
                        raise DownloadCancelled("Đã hủy tải theo yêu cầu.")
            jobs.finish(self.job_id, status="completed")
            self.jobFinished.emit(self.job_id, True, "")
        except DownloadCancelled as exc:
            error_message = str(exc)
            if database is not None and claimed:
                try:
                    DownloadQueueService(database).finish(
                        self.job_id,
                        status="cancelled",
                        error_message=error_message,
                    )
                except Exception as status_error:
                    error_message += f"; không lưu được trạng thái hủy: {status_error}"
            self.jobFinished.emit(self.job_id, False, error_message)
        except Exception as exc:
            error_message = str(exc)
            if database is not None and claimed:
                try:
                    DownloadQueueService(database).finish(
                        self.job_id, status="failed", error_message=error_message
                    )
                except Exception as status_error:
                    error_message += f"; không lưu được trạng thái lỗi: {status_error}"
            elif database is not None:
                try:
                    DownloadQueueService(database).fail_unclaimed(
                        self.job_id, error_message
                    )
                except Exception as status_error:
                    error_message += f"; không lưu được trạng thái lỗi: {status_error}"
            self.jobFinished.emit(self.job_id, False, error_message)
        finally:
            if database is not None:
                database.close()
