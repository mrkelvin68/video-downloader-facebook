import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.validators import validate_output_dir, validate_windows_name
from app.database.connection import Database
from app.models.project import Project


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve())).casefold()


class ProjectService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_projects(self) -> list[Project]:
        return [Project.from_row(row) for row in self.database.list_projects()]

    def create(
        self, name: str, prefix: str, output_dir: str, notes: str = ""
    ) -> str:
        name = validate_windows_name(name, "Tên dự án")
        prefix = validate_windows_name(prefix, "Tiền tố")
        folder = validate_output_dir(output_dir, may_exist=False)
        projects = self.database.list_projects()
        self._ensure_unique(name, folder, projects)
        project_id = str(uuid.uuid4())
        now = _now()
        try:
            folder.mkdir()
        except OSError as exc:
            raise ValueError(
                "Không thể tạo thư mục xuất mà không ghi đè dữ liệu"
            ) from exc
        if not os.access(folder, os.W_OK):
            self._remove_empty_folder(folder)
            raise ValueError("Thư mục xuất không có quyền ghi")
        try:
            self.database.insert_project(
                (project_id, name, prefix, str(folder), notes.strip(), now, now)
            )
        except sqlite3.IntegrityError as exc:
            self._remove_empty_folder(folder)
            raise ValueError("Tên dự án hoặc thư mục xuất đã tồn tại") from exc
        except sqlite3.Error:
            self._remove_empty_folder(folder)
            raise
        return project_id

    def update(
        self,
        project_id: str,
        name: str,
        prefix: str,
        output_dir: str,
        notes: str = "",
    ) -> None:
        current = self.database.get_project(project_id)
        if current is None:
            raise ValueError("Dự án không tồn tại")
        name = validate_windows_name(name, "Tên dự án")
        prefix = validate_windows_name(prefix, "Tiền tố")
        folder = validate_output_dir(output_dir, may_exist=True)
        current_folder = Path(current["output_dir"]).resolve()
        folder_changed = _path_key(folder) != _path_key(current_folder)
        if folder_changed and folder.exists():
            raise ValueError("Thư mục xuất đã tồn tại và không thuộc dự án này.")
        has_videos = self.database.project_has_videos(project_id)
        if has_videos and folder_changed:
            raise ValueError(
                "Không thể đổi thư mục của dự án đã có video nếu chưa hỗ trợ migration."
            )
        if has_videos and prefix != current["prefix"]:
            raise ValueError(
                "Không thể đổi prefix của dự án đã có video vì mã video sẽ thay đổi."
            )
        projects = [
            row for row in self.database.list_projects() if row["uuid"] != project_id
        ]
        self._ensure_unique(name, folder, projects)
        made_folder = False
        if not folder.exists():
            try:
                folder.mkdir()
                made_folder = True
            except OSError as exc:
                raise ValueError(
                    "Không thể tạo thư mục xuất mà không ghi đè dữ liệu"
                ) from exc
        try:
            self.database.update_project(
                project_id,
                (name, prefix, str(folder), notes.strip(), _now()),
            )
        except sqlite3.IntegrityError as exc:
            if made_folder:
                self._remove_empty_folder(folder)
            raise ValueError("Tên dự án hoặc thư mục xuất đã tồn tại") from exc
        except sqlite3.Error:
            if made_folder:
                self._remove_empty_folder(folder)
            raise

    @staticmethod
    def _ensure_unique(name: str, folder: Path, projects: list) -> None:
        for project in projects:
            if project["name"].casefold() == name.casefold():
                raise ValueError("Tên dự án đã tồn tại")
            if _path_key(project["output_dir"]) == _path_key(folder):
                raise ValueError("Thư mục xuất đã tồn tại trong dự án khác")

    @staticmethod
    def _remove_empty_folder(folder: Path) -> None:
        if folder.is_dir():
            if not any(folder.iterdir()):
                folder.rmdir()
