import sqlite3
from pathlib import Path

from app.core.exceptions import VideoImportValidationError
from app.core.validators import normalize_video_url, validate_windows_name
from app.database.connection import Database
from app.models.video import Video
from app.services.csv_service import (
    export_video_csv,
    parse_and_validate_video_csv,
)
from app.services.video_download_provider import provider_for_url


class VideoService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_videos(self, project_id: str) -> list[Video]:
        return [Video.from_row(row) for row in self.database.list_videos(project_id)]

    def import_csv(
        self,
        project_id: str,
        text: str,
        *,
        replace_confirmed: bool = False,
        append: bool = False,
    ) -> int:
        rows = parse_and_validate_video_csv(text)
        project = self.database.get_project(project_id)
        if project is None:
            raise ValueError("Dự án không tồn tại")
        rows = [
            {
                **row,
                "input_row": input_row,
                "normalized_url": normalize_video_url(str(row["url"])),
                "platform": provider.platform.value if (provider := provider_for_url(str(row["url"]))) else None,
                "canonical_url": provider.normalized_url if provider else None,
                "external_video_id": provider.video_id if provider else None,
            }
            for input_row, row in enumerate(rows, start=1)
        ]
        self._validate_unique_urls(project_id, rows, check_saved=append)
        try:
            if append:
                return self.database.append_videos(project_id, rows)
            return self.database.replace_videos(
                project_id, rows, replace_confirmed=replace_confirmed
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Không thể lưu danh sách video do ràng buộc dữ liệu.") from exc

    def _validate_unique_urls(
        self, project_id: str, rows: list[dict[str, object]], *, check_saved: bool
    ) -> None:
        """Reject only canonical URL collisions, preserving all row context."""
        errors: list[str] = []
        first_rows: dict[str, dict[str, object]] = {}
        for row in rows:
            canonical_url = str(row["normalized_url"])
            first = first_rows.get(canonical_url)
            if first is not None:
                errors.append(
                    self._duplicate_input_error(row, int(first["input_row"]))
                )
            else:
                first_rows[canonical_url] = row
        if check_saved:
            saved_by_url = {
                normalize_video_url(str(video["source_url"])): video
                for video in self.database.list_videos(project_id)
            }
            for row in rows:
                saved = saved_by_url.get(str(row["normalized_url"]))
                if saved is not None:
                    errors.append(self._duplicate_saved_error(row, saved))
        if errors:
            raise VideoImportValidationError(errors)

    @staticmethod
    def _duplicate_input_error(row: dict[str, object], first_input_row: int) -> str:
        input_row = int(row["input_row"])
        return (
            "Lỗi import video\n"
            f"Dòng nhập: {input_row}\n"
            f"URL: {row['url']}\n"
            f"ID dòng nhập: {input_row}\n"
            f"Trùng với dòng nhập: {first_input_row}\n"
            "Nguyên nhân: URL đã xuất hiện trong danh sách nhập."
        )

    @staticmethod
    def _duplicate_saved_error(row: dict[str, object], saved: object) -> str:
        input_row = int(row["input_row"])
        return (
            "Lỗi import video\n"
            f"Dòng nhập: {input_row}\n"
            f"URL: {row['url']}\n"
            f"ID dòng nhập: {input_row}\n"
            f"Trùng với video ID: {saved['video_code']}\n"
            f"STT đã lưu: {saved['sequence_number']}\n"
            "Nguyên nhân: URL đã tồn tại trong dự án."
        )

    def export_csv(self, project_id: str) -> str:
        if self.database.get_project(project_id) is None:
            raise ValueError("Dự án không tồn tại")
        return export_video_csv(self.database.export_video_rows(project_id))

    def set_facebook_uploaded(
        self, project_id: str, video_id: str, uploaded: bool
    ) -> None:
        if not isinstance(uploaded, bool):
            raise ValueError("Trạng thái Facebook không hợp lệ.")
        self.database.set_video_facebook_uploaded(project_id, video_id, uploaded)

    def update_video_field(
        self, project_id: str, video_id: str, field: str, value: object
    ) -> None:
        if field == "video_code":
            if not isinstance(value, str):
                raise ValueError("Mã video không hợp lệ.")
            value = validate_windows_name(value, "Mã video")
        elif field == "title":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Tên video không được để trống.")
            value = value.strip()
        elif field == "thumbnail_path":
            if (
                not isinstance(value, str)
                or not value
                or not Path(value).is_file()
            ):
                raise ValueError("Không tìm thấy tệp ảnh đã chọn.")
        else:
            raise ValueError("Trường video không hợp lệ.")
        try:
            self.database.update_video_field(
                project_id, video_id, field, value
            )
        except sqlite3.IntegrityError as exc:
            if field == "video_code":
                raise ValueError("Mã video đã được sử dụng trong dự án.") from exc
            raise
