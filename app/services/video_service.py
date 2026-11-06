import sqlite3
from pathlib import Path

from app.core.validators import normalize_video_url, validate_windows_name
from app.database.connection import Database
from app.models.video import Video
from app.services.csv_service import (
    export_video_csv,
    parse_and_validate_video_csv,
)


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
            {**row, "normalized_url": normalize_video_url(str(row["url"]))}
            for row in rows
        ]
        try:
            if append:
                return self.database.append_videos(project_id, rows)
            return self.database.replace_videos(
                project_id, rows, replace_confirmed=replace_confirmed
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Video trùng STT, URL hoặc mã trong dự án.") from exc

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
