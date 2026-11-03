"""Qt worker: mỗi luồng tự mở SQLite connection, không chia sẻ UI connection."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.database.connection import Database
from app.services.download_service import DownloadService


class DownloadWorker(QThread):
    progress = Signal(int, int, str)
    finished_batch = Signal()
    failed = Signal(str)

    def __init__(self, database_path: str, project_id: str, parent=None) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.project_id = project_id

    def run(self) -> None:
        db = None
        try:
            db = Database(self.database_path)
            service = DownloadService(db)
            service.recover_interrupted(self.project_id)
            videos = [
                row for row in db.list_videos(self.project_id)
                if row["download_status"] in ("pending", "waiting", "failed")
            ]
            for index, video in enumerate(videos, 1):
                if self.isInterruptionRequested():
                    break
                self.progress.emit(index, len(videos), video["video_code"])
                try:
                    service.download(self.project_id, video["id"])
                except Exception as exc:
                    self.failed.emit(f"{video['video_code']}: {exc}")
        except Exception as exc:
            self.failed.emit(f"Không thể chạy hàng đợi: {exc}")
        finally:
            if db is not None:
                db.close()
            self.finished_batch.emit()
