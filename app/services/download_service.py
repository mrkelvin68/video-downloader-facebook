"""Tải từng Facebook video vào thư mục riêng; không ghi đè file hiện hữu."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from app.database.connection import Database
import imageio_ffmpeg


class DownloadError(RuntimeError):
    pass


class DownloadService:
    def __init__(self, database: Database, *, runner=None) -> None:
        self.database = database
        self.runner = runner or subprocess.run

    def download(self, project_id: str, video_id: str) -> tuple[Path, Path | None]:
        project = self.database.get_project(project_id)
        video = self.database.connection.execute(
            "SELECT * FROM videos WHERE id=? AND project_id=?", (video_id, project_id)
        ).fetchone()
        if project is None or video is None:
            raise ValueError("Dự án hoặc video không tồn tại")
        if video["download_status"] not in ("pending", "waiting", "failed"):
            raise DownloadError("Video không ở trạng thái cho phép tải")
        root = Path(project["output_dir"]).resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = root / video["video_code"]
        if target.exists():
            raise DownloadError(f"Thư mục đích đã tồn tại, không ghi đè: {target}")
        self._set_status(video_id, "downloading")
        try:
            with tempfile.TemporaryDirectory(prefix=".download-", dir=root) as work:
                folder = Path(work)
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                command = [
                    "--ffmpeg-location", ffmpeg,
                    "yt-dlp", "--no-playlist", "--no-progress", "--no-overwrites",
                    "--write-thumbnail", "--convert-thumbnails", "jpg",
                    "-f", "bv*+ba/b", "--merge-output-format", "mp4",
                    "--remux-video", "mp4",
                    "-o", str(folder / (video["video_code"] + ".%(ext)s")),
                    video["source_url"],
                ]
                try:
                    result = self.runner(
                        command, capture_output=True, text=True, timeout=1800, check=False
                    )
                except FileNotFoundError as exc:
                    raise DownloadError("Thiếu yt-dlp. Chạy lại py install.py") from exc
                except subprocess.TimeoutExpired as exc:
                    raise DownloadError("Tải video quá thời gian cho phép") from exc
                if result.returncode != 0:
                    reason = (result.stderr or result.stdout or "Không rõ nguyên nhân").strip()
                    raise DownloadError("yt-dlp thất bại: " + reason[-600:])
                video_path = folder / (video["video_code"] + ".mp4")
                if not video_path.is_file() or video_path.stat().st_size == 0:
                    raise DownloadError("Không tìm thấy MP4 hợp lệ; kiểm tra FFmpeg và nguồn video")
                thumb = folder / (video["video_code"] + ".jpg")
                # Thumbnail có thể không khả dụng ở nguồn; không đánh đồng với video lỗi.
                has_thumbnail = thumb.is_file()
                os.replace(folder, target)
                output = target / video_path.name
                thumbnail = target / thumb.name if has_thumbnail else None
                self._set_status(video_id, "completed", str(output), str(thumbnail) if thumbnail else None)
                return output, thumbnail
        except BaseException:
            self._set_status(video_id, "failed")
            raise

    def _set_status(self, video_id: str, status: str, video_path=None, thumb_path=None) -> None:
        from datetime import UTC, datetime
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE videos
                   SET download_status=?, local_video_path=?, thumbnail_path=?, updated_at=?
                   WHERE id=?""",
                (status, video_path, thumb_path, datetime.now(UTC).isoformat(timespec="seconds"), video_id),
            )

    def recover_interrupted(self, project_id: str) -> int:
        """Trạng thái đang tải sau khi app crash không phải completed."""
        with self.database.connection:
            cursor = self.database.connection.execute(
                """UPDATE videos SET download_status='failed'
                   WHERE project_id=? AND download_status='downloading'""", (project_id,)
            )
            return cursor.rowcount
