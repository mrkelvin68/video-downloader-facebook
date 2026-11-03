"""Runs one persisted queue job on a thread-owned SQLite connection."""
from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import QThread, Signal

from app.database.connection import Database
from app.services.download_queue_service import DownloadQueueService
from app.services.download_service import DownloadService


class DownloadWorker(QThread):
    progress = Signal(str, float, int, object, object, object)
    jobFinished = Signal(str, bool, str)

    def __init__(self, database_path: str, job_id: str, parent=None) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.job_id = job_id

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

            def report_progress(
                percent: float,
                downloaded: int,
                total: int | None,
                speed: float | None,
                eta: int | None,
            ) -> None:
                jobs.update_progress(
                    self.job_id,
                    progress=percent,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    speed=speed,
                )
                self.progress.emit(
                    self.job_id, percent, downloaded, total, speed, eta
                )

            DownloadService(database).download(
                job["project_id"],
                job["video_id"],
                progress_callback=report_progress,
            )
            jobs.finish(self.job_id, status="completed")
            self.jobFinished.emit(self.job_id, True, "")
        except Exception as exc:
            error_message = str(exc)
            if database is not None and claimed:
                try:
                    DownloadQueueService(database).finish(
                        self.job_id, status="failed", error_message=error_message
                    )
                    with database.connection:
                        database.connection.execute(
                            """UPDATE videos
                               SET download_status='failed', updated_at=?
                               WHERE id=(SELECT video_id FROM download_jobs WHERE job_id=?)
                                 AND project_id=(SELECT project_id FROM download_jobs WHERE job_id=?)
                                 AND download_status='downloading'""",
                            (
                                datetime.now(UTC).isoformat(timespec="seconds"),
                                self.job_id,
                                self.job_id,
                            ),
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
