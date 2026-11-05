import os
import sqlite3
import shutil
import stat
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.validators import validate_output_dir, validate_windows_name
from app.database.connection import Database
from app.models.project import Project


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve())).casefold()


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink() or (
        hasattr(os.path, "isjunction") and os.path.isjunction(path)
    ):
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


@dataclass(frozen=True)
class ProjectDeleteResult:
    metadata_deleted: bool
    files_deleted: bool
    remaining_files_path: str | None = None
    cleanup_error: str | None = None


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

    def deletion_details(self, project_id: str) -> dict[str, int | str]:
        project = self.database.get_project(project_id)
        if project is None:
            raise ValueError("Dự án không tồn tại")
        connection = self.database.connection
        return {
            "name": project["name"],
            "output_dir": project["output_dir"],
            "video_count": connection.execute(
                "SELECT COUNT(*) FROM videos WHERE project_id=?", (project_id,)
            ).fetchone()[0],
            "job_count": connection.execute(
                "SELECT COUNT(*) FROM download_jobs WHERE project_id=?",
                (project_id,),
            ).fetchone()[0],
        }

    def delete(
        self, project_id: str, *, delete_files: bool = False
    ) -> ProjectDeleteResult:
        connection = self.database.connection
        project = self.database.get_project(project_id)
        if project is None:
            raise ValueError("Dự án không tồn tại")
        active = connection.execute(
            """SELECT 1 FROM download_jobs
               WHERE project_id=? AND status='downloading'
               LIMIT 1""",
            (project_id,),
        ).fetchone()
        if active is not None:
            raise ValueError("Cần dừng các job đang hoạt động trước khi xóa dự án.")

        directory = None
        staged_directory = None
        if delete_files:
            directory = self._safe_project_directory(project)
            if directory.exists():
                staged_directory = directory.with_name(
                    f".{directory.name}.delete-{uuid.uuid4().hex}"
                )
                directory.rename(staged_directory)
                try:
                    self._reject_reparse_points(staged_directory)
                except BaseException:
                    try:
                        staged_directory.rename(directory)
                    except OSError as restore_error:
                        raise RuntimeError(
                            "Phát hiện symlink/junction sau khi khóa thư mục; "
                            f"dữ liệu đang được giữ tại {staged_directory}: "
                            f"{restore_error}"
                        ) from restore_error
                    raise

        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT uuid FROM projects WHERE uuid=?", (project_id,)
            ).fetchone()
            if current is None:
                raise ValueError("Dự án không tồn tại")
            active = connection.execute(
                """SELECT 1 FROM download_jobs
                   WHERE project_id=? AND status='downloading'
                   LIMIT 1""",
                (project_id,),
            ).fetchone()
            if active is not None:
                raise ValueError(
                    "Dự án phát sinh job đang hoạt động; không thể xóa."
                )
            self._delete_project_metadata(connection, project_id)
            connection.commit()
        except BaseException:
            connection.rollback()
            if staged_directory is not None and staged_directory.exists():
                try:
                    staged_directory.rename(directory)
                except OSError as restore_error:
                    raise RuntimeError(
                        "Không xóa được metadata; thư mục dự án đang được giữ tại "
                        f"{staged_directory}: {restore_error}"
                    ) from restore_error
            raise

        if not delete_files or staged_directory is None:
            return ProjectDeleteResult(
                metadata_deleted=True, files_deleted=delete_files
            )
        try:
            shutil.rmtree(staged_directory)
        except OSError as exc:
            return ProjectDeleteResult(
                metadata_deleted=True,
                files_deleted=False,
                remaining_files_path=str(staged_directory),
                cleanup_error=str(exc),
            )
        return ProjectDeleteResult(metadata_deleted=True, files_deleted=True)

    def _safe_project_directory(self, project) -> Path:
        stored_path = Path(project["output_dir"]).expanduser()
        if not stored_path.is_absolute():
            raise ValueError("Đường dẫn dự án không phải đường dẫn tuyệt đối.")
        directory = Path(os.path.abspath(stored_path))
        canonical = directory.resolve(strict=False)
        if canonical == Path(canonical.anchor):
            raise ValueError("Không thể xóa thư mục gốc.")

        protected_paths = [
            Path.home().resolve(),
            Path(__file__).resolve().parents[2],
        ]
        if getattr(sys, "frozen", False):
            protected_paths.append(Path(sys.executable).resolve().parent)
        if any(
            canonical == protected or canonical in protected.parents
            for protected in protected_paths
        ):
            raise ValueError(
                "Không thể xóa thư mục ứng dụng/người dùng hoặc thư mục cha."
            )
        database_parent = Path(self.database.path).resolve().parent
        if canonical == database_parent or canonical in database_parent.parents:
            raise ValueError("Không thể xóa thư mục ứng dụng hoặc thư mục cha.")

        other_projects = self.database.connection.execute(
            "SELECT output_dir FROM projects WHERE uuid<>?", (project["uuid"],)
        )
        for row in other_projects:
            other = Path(row["output_dir"]).resolve(strict=False)
            if (
                canonical == other
                or canonical in other.parents
                or other in canonical.parents
            ):
                raise ValueError(
                    "Thư mục dự án nằm ngoài phạm vi riêng của dự án này."
                )

        for parent in (directory, *directory.parents):
            if parent.exists() and _is_reparse_point(parent):
                raise ValueError(
                    f"Không xóa thư mục đi qua symlink/junction: {parent}"
                )
        if directory.exists():
            if not directory.is_dir():
                raise ValueError("Đường dẫn đầu ra của dự án không phải thư mục.")
            self._reject_reparse_points(directory)
        return canonical

    @classmethod
    def _reject_reparse_points(cls, directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if _is_reparse_point(path):
                    raise ValueError(
                        f"Không xóa dự án có symlink/junction: {path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    cls._reject_reparse_points(path)

    @staticmethod
    def _delete_project_metadata(
        connection: sqlite3.Connection, project_id: str
    ) -> None:
        for table, key in (
            ("matrix_undo_snapshots", "matrix_id"),
            ("matrix_entries", "matrix_id"),
            ("matrix_slots", "matrix_id"),
            ("matrix_pages", "matrix_id"),
            ("matrices", "matrix_id"),
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE {key} IN "
                "(SELECT matrix_id FROM matrices WHERE project_id=?)",
                (project_id,),
            )
        connection.execute("DELETE FROM download_jobs WHERE project_id=?", (project_id,))
        connection.execute("DELETE FROM facebook_publishing_jobs WHERE project_id=?", (project_id,))
        connection.execute("DELETE FROM facebook_drafts WHERE project_id=?", (project_id,))
        connection.execute("DELETE FROM facebook_pages WHERE project_id=?", (project_id,))
        connection.execute("DELETE FROM videos WHERE project_id=?", (project_id,))
        connection.execute("DELETE FROM projects WHERE uuid=?", (project_id,))

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
