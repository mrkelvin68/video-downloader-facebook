"""Tải một Facebook video, xác minh đầu ra rồi lưu vào SQLite."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.database.connection import Database


class DownloadError(RuntimeError):
    pass


class DownloadService:
    def __init__(self, database: Database, *, runner=None, ffprobe=None) -> None:
        self.database = database
        self.runner = runner or subprocess.run
        self.ffprobe = ffprobe or self._find_ffprobe

    def download(self, project_id: str, video_id: str) -> tuple[Path, Path | None]:
        project = self.database.get_project(project_id)
        video = self.database.connection.execute(
            "SELECT * FROM videos WHERE id=? AND project_id=?", (video_id, project_id)
        ).fetchone()
        if project is None or video is None:
            raise ValueError("Dự án hoặc video không tồn tại")
        if video["download_status"] == "completed":
            raise DownloadError("Video đã tải, không tải lại")
        if video["download_status"] not in ("pending", "waiting", "failed"):
            raise DownloadError("Video không ở trạng thái cho phép tải")

        root = Path(project["output_dir"]).resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = root / video["video_code"]
        if target.exists():
            raise DownloadError(f"Thư mục đích đã tồn tại, không ghi đè: {target}")

        self._set_status(project_id, video_id, "downloading")
        try:
            with tempfile.TemporaryDirectory(prefix=".download-", dir=root) as work:
                folder = Path(work)
                ffmpeg_loc = self._find_ffmpeg()
                command = [
                    sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-progress",
                    "--no-overwrites", "--write-thumbnail", "--convert-thumbnails", "jpg",
                    "-f", "bv*+ba/b", "--merge-output-format", "mp4", "--remux-video", "mp4",
                ]
                if ffmpeg_loc:
                    command.extend(["--ffmpeg-location", ffmpeg_loc])
                command.extend([
                    "-o", str(folder / (video["video_code"] + ".%(ext)s")),
                    video["source_url"],
                ])

                try:
                    result = self.runner(command, capture_output=True, text=True, timeout=1800, check=False)
                except FileNotFoundError as exc:
                    raise DownloadError("Không tìm thấy Python/yt-dlp. Chạy lại py install.py") from exc
                except subprocess.TimeoutExpired as exc:
                    raise DownloadError("Tải video quá thời gian cho phép") from exc
                if result.returncode != 0:
                    reason = (result.stderr or result.stdout or "Không rõ nguyên nhân").strip()
                    raise DownloadError("yt-dlp thất bại: " + reason[-600:])

                video_path = folder / f"{video['video_code']}.mp4"
                if not video_path.is_file() or video_path.stat().st_size == 0:
                    raise DownloadError("Không tìm thấy MP4 sau khi tải")
                self._validate_media(video_path)

                # Thumbnail chuẩn hóa thành thumbnail.jpg nếu có
                thumb_candidates = list(folder.glob(f"{video['video_code']}.*"))
                has_thumbnail = False
                for cand in thumb_candidates:
                    if cand.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and cand.stat().st_size > 0:
                        dest_thumb = folder / "thumbnail.jpg"
                        if cand != dest_thumb:
                            if dest_thumb.exists():
                                dest_thumb.unlink()
                            cand.rename(dest_thumb)
                        has_thumbnail = True
                        break

                os.replace(folder, target)
                output = target / video_path.name
                thumbnail = target / "thumbnail.jpg" if has_thumbnail else None
                self._set_status(project_id, video_id, "completed", str(output), str(thumbnail) if thumbnail else None)
                return output, thumbnail
        except Exception as download_error:
            try:
                self._set_status(project_id, video_id, "failed")
            except Exception as status_error:
                raise DownloadError(
                    f"{download_error}; không thể lưu trạng thái thất bại: {status_error}"
                ) from status_error
            raise

    def _validate_media(self, path: Path) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise DownloadError("Tệp MP4 rỗng hoặc không tồn tại")
        probe = self.ffprobe()
        if not probe:
            raise DownloadError("Không tìm thấy FFprobe; cài FFmpeg hoặc đặt FFPROBE trong PATH")
        try:
            result = self.runner([
                probe, "-v", "error", "-show_entries", "format=duration,size",
                "-show_streams", "-of", "json", str(path),
            ], capture_output=True, text=True, timeout=60, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise DownloadError("FFprobe không thể đọc file MP4") from exc
        if result.returncode != 0:
            raise DownloadError("FFprobe thất bại: " + (result.stderr or "Không rõ nguyên nhân").strip()[-400:])
        try:
            metadata = json.loads(result.stdout)
            streams = metadata.get("streams", [])
            format_info = metadata.get("format", {})
            duration = float(format_info.get("duration", 0))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DownloadError("FFprobe trả về metadata không hợp lệ") from exc

        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        if not video_streams or duration <= 0:
            raise DownloadError("MP4 không có video stream hoặc duration không hợp lệ")

    @staticmethod
    def _find_ffmpeg() -> str | None:
        configured = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
        if configured:
            return configured
        try:
            from static_ffmpeg import run
            ffmpeg, _ = run.get_or_fetch_platform_executables_else_raise()
            return ffmpeg
        except Exception:
            return None

    @staticmethod
    def _find_ffprobe() -> str | None:
        configured = os.environ.get("FFPROBE") or shutil.which("ffprobe")
        if configured:
            return configured
        try:
            from static_ffmpeg import run
            _ffmpeg, ffprobe = run.get_or_fetch_platform_executables_else_raise()
            return ffprobe
        except Exception:
            return None

    def _set_status(
        self, project_id: str, video_id: str, status: str, video_path: str | None = None, thumb_path: str | None = None
    ) -> None:
        from datetime import UTC, datetime
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE videos
                   SET download_status=?, local_video_path=?, thumbnail_path=?, updated_at=?
                   WHERE id=? AND project_id=?""",
                (status, video_path, thumb_path, datetime.now(UTC).isoformat(timespec="seconds"), video_id, project_id),
            )


    def recover_interrupted(self, project_id: str) -> int:
        with self.database.connection:
            cursor = self.database.connection.execute(
                "UPDATE videos SET download_status='failed' WHERE project_id=? AND download_status='downloading'",
                (project_id,),
            )
            return cursor.rowcount