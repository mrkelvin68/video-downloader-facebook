from pathlib import Path

from app.core.validators import (
    normalize_video_url,
    validate_facebook_url,
    validate_output_dir,
    validate_windows_name,
)
from app.database.connection import Database
from app.services.csv_service import parse_video_csv, sample_video_csv
from app.services.project_service import ProjectService
from app.services.video_service import VideoService

APP_DIR = Path.home() / ".video-downloader-facebook"
DB_PATH = APP_DIR / "projects.sqlite3"


class ProjectStore:
    """Compatibility facade for callers of the original single-file app."""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.database = Database(path)
        self.connection = self.database.connection
        self.project_service = ProjectService(self.database)
        self.video_service = VideoService(self.database)

    def list_projects(self):
        return self.database.list_projects()

    def create(self, name: str, prefix: str, output_dir: str, notes: str = "") -> str:
        return self.project_service.create(name, prefix, output_dir, notes)

    def update(
        self,
        project_id: str,
        name: str,
        prefix: str,
        output_dir: str,
        notes: str = "",
    ) -> None:
        self.project_service.update(project_id, name, prefix, output_dir, notes)

    def import_csv(
        self, project_id: str, text: str, *, replace_confirmed: bool = False
    ) -> int:
        return self.video_service.import_csv(
            project_id, text, replace_confirmed=replace_confirmed
        )

    def list_videos(self, project_id: str):
        return self.database.list_videos(project_id)

    def export_csv(self, project_id: str) -> str:
        return self.video_service.export_csv(project_id)

    def close(self) -> None:
        self.database.close()


VideoStore = ProjectStore


def main() -> int:
    from app.ui.main_window import main as run_application

    return run_application()


def __getattr__(name: str):
    if name == "MainWindow":
        from app.ui.main_window import MainWindow

        return MainWindow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "Database",
    "MainWindow",
    "ProjectStore",
    "VideoStore",
    "normalize_video_url",
    "parse_video_csv",
    "sample_video_csv",
    "validate_facebook_url",
    "validate_output_dir",
    "validate_windows_name",
    "main",
]
