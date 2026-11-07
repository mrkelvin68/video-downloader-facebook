"""Tải một Facebook video, xác minh đầu ra rồi lưu vào SQLite."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from app.database.connection import Database
from app.services.video_download_provider import VideoDownloadProvider, provider_for_url


class DownloadError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        inferred_category, inferred_retryable = classify_download_error(message)
        self.category = category or inferred_category
        self.retryable = (
            inferred_retryable if retryable is None else retryable
        )
        super().__init__(f"[{self.category}] {message}")


class DownloadCancelled(RuntimeError):
    pass


def classify_download_error(message: str) -> tuple[str, bool]:
    normalized = message.casefold()
    if any(
        marker in normalized
        for marker in ("timed out", "timeout", "quá thời gian", "hết thời gian")
    ):
        return "timeout", True
    if any(
        marker in normalized
        for marker in (
            "ffmpeg",
            "ffprobe",
            "postprocessing",
            "merging formats",
            "remux",
        )
    ):
        return "ffmpeg", False
    if any(
        marker in normalized
        for marker in (
            "login required",
            "log in",
            "login",
            "authentication",
            "cookies",
            "checkpoint",
            "not authorized",
            "http error 401",
            "http error 403",
        )
    ):
        return "authentication", False
    if any(
        marker in normalized
        for marker in (
            "404",
            "not found",
            "video unavailable",
            "content is not available",
            "private",
            "removed",
            "deleted",
            "does not exist",
        )
    ):
        return "unavailable", False
    if any(
        marker in normalized
        for marker in (
            "connection",
            "network",
            "dns",
            "name resolution",
            "ssl",
            "tls",
            "http error 429",
            "http error 5",
            "temporarily unavailable",
            "remote end closed",
            "unable to download webpage",
        )
    ):
        return "network", True
    if "unavailable" in normalized or "not available" in normalized:
        return "unavailable", False
    if any(
        marker in normalized
        for marker in (
            "invalid url",
            "unsupported url",
            "not a valid url",
            "no suitable extractor",
            "not supported",
        )
    ):
        return "unsupported", False
    if "no module named yt_dlp" in normalized or "yt-dlp executable" in normalized:
        return "environment", False
    return "other", False


class DownloadService:
    def __init__(self, database: Database, *, runner=None, ffprobe=None) -> None:
        self.database = database
        self.runner = runner or subprocess.run
        self.ffprobe = ffprobe or self._find_ffprobe

    def download(
        self,
        project_id: str,
        video_id: str,
        *,
        progress_callback: Callable[[float, int, int | None, float | None, int | None], None]
        | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[Path, Path | None]:
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

        provider = provider_for_url(str(video["source_url"]))
        source_url = provider.normalized_url if provider else str(video["source_url"])

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
                    *self._yt_dlp_command(), "--no-playlist",
                    "--no-overwrites", "--write-thumbnail", "--write-info-json", "--convert-thumbnails", "jpg",
                    "-f", "bv*+ba/b", "--merge-output-format", "mp4", "--remux-video", "mp4",
                ]
                if progress_callback is None:
                    command.append("--no-progress")
                else:
                    command.extend(
                        [
                            "--newline",
                            "--progress-delta",
                            "1",
                            "--progress-template",
                            "download:QUEUE_PROGRESS:%(progress._percent_str)s\t"
                            "%(progress.downloaded_bytes)s\t"
                            "%(progress.total_bytes or progress.total_bytes_estimate)s\t"
                            "%(progress.speed)s\t%(progress.eta)s",
                        ]
                    )
                if ffmpeg_loc:
                    command.extend(["--ffmpeg-location", ffmpeg_loc])
                command.extend([
                    "-o", str(folder / (video["video_code"] + ".%(ext)s")),
                    source_url,
                ])

                try:
                    if progress_callback is None:
                        if cancel_event is not None and cancel_event.is_set():
                            raise DownloadCancelled("Đã hủy tải theo yêu cầu.")
                        result = self.runner(
                            command,
                            capture_output=True,
                            text=True,
                            timeout=1800,
                            check=False,
                        )
                    else:
                        result = self._run_with_progress(
                            command, progress_callback, cancel_event
                        )
                except FileNotFoundError as exc:
                    raise DownloadError(
                        "Không tìm thấy Python/yt-dlp. Chạy lại py install.py",
                        category="environment",
                    ) from exc
                except subprocess.TimeoutExpired as exc:
                    raise DownloadError(
                        "Tải video quá thời gian cho phép",
                        category="timeout",
                        retryable=True,
                    ) from exc
                if result.returncode != 0:
                    reason = (result.stderr or result.stdout or "Không rõ nguyên nhân").strip()
                    raise DownloadError("yt-dlp thất bại: " + reason[-600:])

                video_path = folder / f"{video['video_code']}.mp4"
                if not video_path.is_file() or video_path.stat().st_size == 0:
                    raise DownloadError("Không tìm thấy MP4 sau khi tải")
                self._validate_media(video_path)
                metadata = self._read_download_metadata(
                    folder / f"{video['video_code']}.info.json", provider
                )

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
                self._set_video_metadata(project_id, video_id, metadata)
                self._set_status(project_id, video_id, "completed", str(output), str(thumbnail) if thumbnail else None)
                return output, thumbnail
        except Exception as download_error:
            try:
                self._set_status(project_id, video_id, "failed")
            except Exception as status_error:
                raise DownloadError(
                    f"{download_error}; không thể lưu trạng thái thất bại: {status_error}",
                    category="other",
                    retryable=False,
                ) from status_error
            raise

    @staticmethod
    def _run_with_progress(
        command: list[str],
        progress_callback: Callable[
            [float, int, int | None, float | None, int | None], None
        ],
        cancel_event: threading.Event | None = None,
    ) -> subprocess.CompletedProcess:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            raise

        output_lines: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            stdout = process.stdout
            try:
                if stdout is not None:
                    for line in stdout:
                        output_lines.put(line.rstrip("\r\n"))
            finally:
                close_stdout = getattr(stdout, "close", None)
                if callable(close_stdout):
                    close_stdout()
                output_lines.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + 1800
        output_tail: list[str] = []
        reader_finished = False
        while not reader_finished or process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                DownloadService._terminate_process_tree(process)
                reader.join(timeout=5)
                raise DownloadCancelled("Đã hủy tải theo yêu cầu.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                DownloadService._terminate_process_tree(process)
                reader.join(timeout=5)
                raise subprocess.TimeoutExpired(command, 1800)
            try:
                line = output_lines.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if line is None:
                reader_finished = True
                continue
            output_tail.append(line)
            if len(output_tail) > 100:
                output_tail.pop(0)
            if not line.startswith("QUEUE_PROGRESS:"):
                continue
            fields = line.removeprefix("QUEUE_PROGRESS:").split("\t")
            if len(fields) != 5:
                continue
            try:
                percent = float(fields[0].strip().rstrip("%"))
                downloaded = int(fields[1]) if fields[1].isdigit() else 0
                total = int(fields[2]) if fields[2].isdigit() else None
                speed = float(fields[3]) if fields[3] not in ("None", "") else None
                eta = int(fields[4]) if fields[4].isdigit() else None
            except ValueError:
                continue
            try:
                progress_callback(percent, downloaded, total, speed, eta)
            except BaseException:
                DownloadService._terminate_process_tree(process)
                reader.join(timeout=5)
                raise

        reader.join(timeout=5)
        return subprocess.CompletedProcess(
            command,
            process.wait(),
            stdout="\n".join(output_tail),
            stderr="",
        )

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        if os.name == "nt" and process.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _validate_media(self, path: Path) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise DownloadError("Tệp MP4 rỗng hoặc không tồn tại")
        probe = self.ffprobe()
        if not probe:
            raise DownloadError(
                "Không tìm thấy FFprobe; cài FFmpeg hoặc đặt FFPROBE trong PATH",
                category="ffmpeg",
            )
        try:
            result = self.runner([
                probe, "-v", "error", "-show_entries", "format=duration,size",
                "-show_streams", "-of", "json", str(path),
            ], capture_output=True, text=True, timeout=60, check=False)
        except FileNotFoundError as exc:
            raise DownloadError(
                "Không thể chạy FFprobe", category="ffmpeg"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DownloadError(
                "FFprobe hết thời gian đọc file MP4",
                category="timeout",
                retryable=True,
            ) from exc
        if result.returncode != 0:
            raise DownloadError(
                "FFprobe thất bại: "
                + (result.stderr or "Không rõ nguyên nhân").strip()[-400:],
                category="ffmpeg",
            )
        try:
            metadata = json.loads(result.stdout)
            streams = metadata.get("streams", [])
            format_info = metadata.get("format", {})
            duration = float(format_info.get("duration", 0))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DownloadError(
                "FFprobe trả về metadata không hợp lệ", category="ffmpeg"
            ) from exc

        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        if not video_streams or duration <= 0:
            raise DownloadError(
                "MP4 không có video stream hoặc duration không hợp lệ",
                category="ffmpeg",
            )

    @staticmethod
    def _yt_dlp_command() -> list[str]:
        if getattr(sys, "frozen", False):
            bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "yt-dlp.exe"
            if bundled.is_file():
                return [str(bundled)]
            return [sys.executable, "--yt-dlp"]
        return [sys.executable, "-m", "yt_dlp"]

    @staticmethod
    def _find_ffmpeg() -> str | None:
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            for candidate in (base / "ffmpeg.exe", base / "static_ffmpeg" / "bin" / "win32" / "ffmpeg.exe"):
                if candidate.is_file():
                    return str(candidate)
            return None
        configured = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
        if configured:
            return configured
        tools = DownloadService._find_static_ffmpeg()
        return tools[0] if tools else None

    @staticmethod
    def _find_static_ffmpeg() -> tuple[str, str] | None:
        try:
            from static_ffmpeg import run
            from requests.exceptions import RequestException
        except ImportError:
            return None
        try:
            return run.get_or_fetch_platform_executables_else_raise()
        except RequestException as exc:
            raise DownloadError(str(exc) or type(exc).__name__) from exc
        except (OSError, zipfile.BadZipFile) as exc:
            raise DownloadError(
                f"FFmpeg/FFprobe không khả dụng: {exc}", category="ffmpeg"
            ) from exc

    @staticmethod
    def _find_ffprobe() -> str | None:
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            for candidate in (base / "ffprobe.exe", base / "static_ffmpeg" / "bin" / "win32" / "ffprobe.exe"):
                if candidate.is_file():
                    return str(candidate)
            return None
        configured = os.environ.get("FFPROBE") or shutil.which("ffprobe")
        if configured:
            return configured
        tools = DownloadService._find_static_ffmpeg()
        return tools[1] if tools else None

    @staticmethod
    def _read_download_metadata(
        info_path: Path, provider: VideoDownloadProvider | None
    ) -> dict[str, object]:
        if not info_path.is_file():
            return {
                "platform": provider.platform.value if provider else None,
                "canonical_url": provider.normalized_url if provider else None,
                "external_video_id": provider.video_id if provider else None,
            }
        try:
            payload = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {
                "platform": provider.platform.value if provider else None,
                "canonical_url": provider.normalized_url if provider else None,
                "external_video_id": provider.video_id if provider else None,
            }
        width, height = payload.get("width"), payload.get("height")
        resolution = f"{width}x{height}" if width and height else None
        return {
            "platform": provider.platform.value if provider else payload.get("extractor_key"),
            "canonical_url": provider.normalized_url if provider else payload.get("webpage_url"),
            "external_video_id": payload.get("id") or (provider.video_id if provider else None),
            "author_name": payload.get("channel") or payload.get("uploader"),
            "duration_seconds": payload.get("duration"),
            "resolution": resolution,
        }

    def _set_video_metadata(
        self, project_id: str, video_id: str, metadata: dict[str, object]
    ) -> None:
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE videos
                   SET platform=COALESCE(?, platform),
                       canonical_url=COALESCE(?, canonical_url),
                       external_video_id=COALESCE(?, external_video_id),
                       author_name=COALESCE(?, author_name),
                       duration_seconds=COALESCE(?, duration_seconds),
                       resolution=COALESCE(?, resolution),
                       updated_at=?
                   WHERE id=? AND project_id=?""",
                (
                    metadata.get("platform"), metadata.get("canonical_url"),
                    metadata.get("external_video_id"), metadata.get("author_name"),
                    metadata.get("duration_seconds"), metadata.get("resolution"),
                    datetime.now(UTC).isoformat(timespec="seconds"), video_id, project_id,
                ),
            )

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
