"""Qt worker: tải duy nhất một video được chọn, mỗi luồng tự mở Database riêng."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.database.connection import Database
from app.services.download_service import DownloadService


class DownloadWorker(QThread):
    progress = Signal(str)
    finished_batch = Signal()
    failed = Signal(str)

    def __init__(self, database_path: str, project_id: str, video_id: str, parent=None) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.project_id = project_id
        self.video_id = video_id

    def run(self) -> None:
        db = None
        try:
            db = Database(self.database_path)
            service = DownloadService(db)
            service.recover_interrupted(self.project_id)
            video = db.connection.execute(
                "SELECT video_code FROM videos WHERE id=? AND project_id=?",
                (self.video_id, self.project_id),
            ).fetchone()
            code = video["video_code"] if video else self.video_id
            self.progress.emit(code)
            service.download(self.project_id, self.video_id)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if db is not None:
                db.close()
            self.finished_batch.emit()
