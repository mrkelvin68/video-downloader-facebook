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
import logging
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.database.connection import Database
from app.services.logging_service import log_download, log_error, redactor
from app.services.video_download_provider import VideoDownloadProvider, VideoPlatform, provider_for_url


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
        for marker in ("cannot parse data", "no video formats found")
    ):
        # Retrying an identical parser failure cannot recover a changed page.
        return "FACEBOOK_EXTRACTOR_FAILED", False
    if any(
        marker in normalized
        for marker in ("http error 403", "http 403", "error 403")
    ):
        return "forbidden", False
    if any(
        marker in normalized
        for marker in ("geo restriction", "not available in your country", "region restricted", "access denied", "forbidden")
    ):
        return "blocked", False
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
            "private video",
            "granted you access",
            "sign in if you've been granted access",
            "this video is private",
            "members-only",
            "join this channel to get access",
        )
    ):
        return "private_access_denied", False
    if any(
        marker in normalized
        for marker in (
            "login required",
            "log in",
            "login",
            "please sign in",
            "sign in to confirm",
            "authentication",
            "cookies",
            "checkpoint",
            "not authorized",
            "http error 401",
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
        provider_name = provider.platform.value if provider else "unknown"
        external_id = provider.video_id if provider else "unknown"

        root = Path(project["output_dir"]).resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = root / video["video_code"]
        if target.exists():
            existing_video = target / f"{video['video_code']}.mp4"
            if video["download_status"] == "failed" and existing_video.is_file():
                self._validate_media(existing_video)
                metadata = self._read_download_metadata(
                    target / f"{video['video_code']}.info.json", provider
                )
                thumbnail = target / "thumbnail.jpg"
                self._set_video_metadata(project_id, video_id, metadata)
                self._set_status(
                    project_id,
                    video_id,
                    "completed",
                    str(existing_video),
                    str(thumbnail) if thumbnail.is_file() else None,
                )
                return existing_video, thumbnail if thumbnail.is_file() else None
            raise DownloadError(f"Thư mục đích đã tồn tại, không ghi đè: {target}")

        self._set_status(project_id, video_id, "downloading")
        try:
            with tempfile.TemporaryDirectory(prefix=".download-", dir=root) as work:
                folder = Path(work)
                ffmpeg_loc = self._find_ffmpeg()
                platform_key = provider.platform.value if provider is not None else None
                cookie_info = self._resolve_cookie_state(platform=platform_key)
                cookies_file = cookie_info["cookies_file"]
                cookies_configured = cookie_info["cookies_configured"]
                cookies_file_found = cookie_info["cookies_file_found"]
                cookies_valid = cookie_info["cookies_valid"]
                yt_dlp_ver = self._yt_dlp_version()

                command = [
                    *self._yt_dlp_command(), "--no-playlist", "--playlist-items", "1",
                    "--no-overwrites", "--write-thumbnail", "--write-info-json", "--convert-thumbnails", "jpg",
                    "-f", "bv*+ba/b", "--merge-output-format", "mp4", "--remux-video", "mp4",
                ]
                cookies_applied = False
                if provider is not None and provider.platform == VideoPlatform.YOUTUBE:
                    js_runtime = self._find_javascript_runtime()
                    if js_runtime:
                        command.extend([
                            "--js-runtimes", f"node:{js_runtime}",
                            "--remote-components", "ejs:github",
                        ])
                if provider is not None and provider.platform in {VideoPlatform.FACEBOOK, VideoPlatform.YOUTUBE}:
                    if cookies_file:
                        command.extend(["--cookies", cookies_file])
                        cookies_applied = True
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
                attempt_number = 1
                log_download(
                    "Starting download: "
                    f"video_code={video['video_code']}, platform={provider_name}, "
                    f"video_id={external_id}, extractor={provider_name}, "
                    f"yt_dlp={yt_dlp_ver}, cookies_configured={cookies_configured}, "
                    f"cookies_file_found={cookies_file_found}, cookies_applied={cookies_applied}, "
                    f"attempt_number={attempt_number}, strategy=canonical_url, url={source_url}"
                )

                def run_ytdlp(candidate_command: list[str]):
                    if progress_callback is None:
                        if cancel_event is not None and cancel_event.is_set():
                            raise DownloadCancelled("Đã hủy tải theo yêu cầu.")
                        return self.runner(
                            candidate_command,
                            capture_output=True,
                            text=True,
                            timeout=1800,
                            check=False,
                        )
                    return self._run_with_progress(
                        candidate_command, progress_callback, cancel_event
                    )

                try:
                    result = run_ytdlp(command)
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
                initial_reason = result.stderr or result.stdout or ""
                facebook_extractor_failure = (
                    result.returncode != 0
                    and provider is not None
                    and provider.platform == VideoPlatform.FACEBOOK
                    and self._is_facebook_extractor_failure(initial_reason)
                )
                if facebook_extractor_failure and self._facebook_redirects_to_login(
                    f"https://m.facebook.com/watch/?v={external_id}&_rdr"
                ):
                    raise DownloadError(
                        "Facebook yêu cầu đăng nhập cho URL này; không tự động đọc cookie hoặc thông tin đăng nhập từ trình duyệt.",
                        category="FACEBOOK_AUTH_REQUIRED",
                        retryable=False,
                    )
                authentication_failure = (
                    result.returncode != 0
                    and provider is not None
                    and provider.platform in {VideoPlatform.FACEBOOK, VideoPlatform.YOUTUBE}
                    and self._requires_facebook_authentication(initial_reason)
                )
                if authentication_failure and cookies_file and not cookies_applied:
                    attempt_number = 2
                    cookies_applied = True
                    log_download(
                        "Retrying download with configured cookies: "
                        f"video_code={video['video_code']}, platform={provider_name}, "
                        f"yt_dlp={yt_dlp_ver}, cookies_configured={cookies_configured}, "
                        f"cookies_file_found={cookies_file_found}, cookies_applied={cookies_applied}, "
                        f"attempt_number={attempt_number}"
                    )
                    cookie_command = [
                        *command[:-1], "--cookies", cookies_file, command[-1]
                    ]
                    result = run_ytdlp(cookie_command)
                if result.returncode != 0:
                    reason = (result.stderr or result.stdout or "Không rõ nguyên nhân").strip()
                    category, retryable = classify_download_error(reason)
                    cookie_hint = ""
                    if cookies_configured and not cookies_file_found:
                        cookie_hint = " (File cookies đã cấu hình không tồn tại trên hệ thống; hãy kiểm tra lại trong Cài đặt → Xác thực tải video.)"
                    elif cookies_configured and not cookies_valid:
                        cookie_hint = " (File cookies đã cấu hình không hợp lệ hoặc không đúng định dạng Netscape; hãy xuất lại file cookies.txt.)"

                    if provider is not None and provider.platform == VideoPlatform.YOUTUBE:
                        if category == "private_access_denied":
                            if cookies_applied:
                                log_download(
                                    f"Download error diagnostic: platform={provider_name}, yt_dlp={yt_dlp_ver}, "
                                    f"cookies_configured={cookies_configured}, cookies_file_found={cookies_file_found}, "
                                    f"cookies_applied={cookies_applied}, attempt_number={attempt_number}, "
                                    "error_category=private_access_denied"
                                )
                                raise DownloadError(
                                    "Video YouTube này ở chế độ Riêng tư (Private) và tài khoản trong file cookies hiện tại "
                                    "chưa được chủ sở hữu cấp quyền xem, hoặc phiên đăng nhập đã hết hạn. "
                                    "Hãy xuất lại cookies từ đúng tài khoản được cấp quyền truy cập video này."
                                    f" ({reason[-300:]})",
                                    category="private_access_denied",
                                    retryable=False,
                                )
                            log_download(
                                f"Download error diagnostic: platform={provider_name}, yt_dlp={yt_dlp_ver}, "
                                f"cookies_configured={cookies_configured}, cookies_file_found={cookies_file_found}, "
                                f"cookies_applied={cookies_applied}, attempt_number={attempt_number}, "
                                "error_category=authentication"
                            )
                            raise DownloadError(
                                "Video YouTube này ở chế độ Riêng tư (Private) và yêu cầu đăng nhập. "
                                "Hãy cấu hình file cookies Netscape hợp lệ của tài khoản được cấp quyền trong Cài đặt → Xác thực tải video."
                                f"{cookie_hint} ({reason[-300:]})",
                                category="authentication",
                                retryable=False,
                            )
                        if category == "forbidden":
                            log_download(
                                f"Download error diagnostic: platform={provider_name}, yt_dlp={yt_dlp_ver}, "
                                f"cookies_configured={cookies_configured}, cookies_file_found={cookies_file_found}, "
                                f"cookies_applied={cookies_applied}, attempt_number={attempt_number}, "
                                "error_category=forbidden"
                            )
                            raise DownloadError(
                                "YouTube từ chối tải media (HTTP 403). Hãy kiểm tra phiên/thiết lập hợp lệ hoặc thử lại sau; ứng dụng không vượt qua cơ chế hạn chế truy cập."
                                f"{cookie_hint}",
                                category="forbidden",
                                retryable=False,
                            )
                        if authentication_failure or category == "authentication":
                            log_download(
                                f"Download error diagnostic: platform={provider_name}, yt_dlp={yt_dlp_ver}, "
                                f"cookies_configured={cookies_configured}, cookies_file_found={cookies_file_found}, "
                                f"cookies_applied={cookies_applied}, attempt_number={attempt_number}, "
                                "error_category=authentication"
                            )
                            raise DownloadError(
                                "YouTube yêu cầu xác thực hoặc đang chặn yêu cầu từ môi trường này. "
                                "Hãy đăng nhập hợp lệ/cấu hình cookie YouTube do bạn cho phép, "
                                "hoặc thử lại sau."
                                f"{cookie_hint} ({reason[-300:]})",
                                category="authentication",
                                retryable=False,
                            )

                    if authentication_failure:
                        log_download(
                            f"Download error diagnostic: platform={provider_name}, yt_dlp={yt_dlp_ver}, "
                            f"cookies_configured={cookies_configured}, cookies_file_found={cookies_file_found}, "
                            f"cookies_applied={cookies_applied}, attempt_number={attempt_number}, "
                            "error_category=FACEBOOK_AUTH_REQUIRED"
                        )
                        raise DownloadError(
                            "Video yêu cầu đăng nhập Facebook (ví dụ: nội dung tài trợ/affiliate hoặc giới hạn độ tuổi) "
                            "hoặc không khả dụng ở chế độ khách. Hãy cung cấp file cookies.txt do bạn chủ động xuất "
                            "tại ~/.video-downloader-facebook/cookies.txt rồi thử lại. "
                            f"Ứng dụng không tự động đọc cookie trình duyệt.{cookie_hint} ({reason[-300:]})",
                            category="FACEBOOK_AUTH_REQUIRED",
                            retryable=False,
                        )
                    if provider is not None and provider.platform == VideoPlatform.FACEBOOK:
                        category = {
                            "network": "FACEBOOK_NETWORK_ERROR",
                            "unavailable": "FACEBOOK_VIDEO_UNAVAILABLE",
                            "private_access_denied": "FACEBOOK_VIDEO_UNAVAILABLE",
                            "FACEBOOK_EXTRACTOR_FAILED": "FACEBOOK_EXTRACTOR_FAILED",
                            "other": "FACEBOOK_DOWNLOAD_FAILED",
                            "unsupported": "FACEBOOK_DOWNLOAD_FAILED",
                            "forbidden": "FACEBOOK_DOWNLOAD_FAILED",
                            "blocked": "FACEBOOK_DOWNLOAD_FAILED",
                        }.get(category, category)
                    log_download(
                        f"Download error diagnostic: platform={provider_name}, yt_dlp={yt_dlp_ver}, "
                        f"cookies_configured={cookies_configured}, cookies_file_found={cookies_file_found}, "
                        f"cookies_applied={cookies_applied}, attempt_number={attempt_number}, "
                        f"error_category={category}"
                    )
                    raise DownloadError(
                        "yt-dlp thất bại: " + reason[-600:],
                        category=category,
                        retryable=retryable,
                    )

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

                for attempt in range(5):
                    try:
                        os.replace(folder, target)
                        break
                    except PermissionError:
                        if target.is_dir() and (target / video_path.name).is_file():
                            break
                        if attempt == 4:
                            raise
                        time.sleep(0.25 * (attempt + 1))
                output = target / video_path.name
                thumbnail = target / "thumbnail.jpg" if has_thumbnail else None
                self._set_video_metadata(project_id, video_id, metadata)
                self._set_status(project_id, video_id, "completed", str(output), str(thumbnail) if thumbnail else None)
                log_download(f"Download completed successfully: video_code={video['video_code']}, path={output}")
                return output, thumbnail
        except Exception as download_error:
            video_code = video["video_code"] if video is not None else "unknown"
            log_download(
                "Download failed: "
                f"video_code={video_code}, platform={provider_name if 'provider_name' in locals() else 'unknown'}, "
                f"video_id={external_id if 'external_id' in locals() else 'unknown'}, "
                f"url={source_url if 'source_url' in locals() else 'unknown'}, result={download_error}",
                level=logging.WARNING,
            )
            log_error(f"Download error on video {video_code}", exc=download_error)
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
    def _is_valid_netscape_cookies_file(path: str | Path) -> bool:
        """Validate cookie file existence, readability, and Netscape format without logging contents."""
        try:
            candidate = Path(path)
            if not candidate.is_file() or candidate.stat().st_size <= 0:
                return False
            sample = candidate.read_text(encoding="utf-8", errors="ignore")[:8192]
        except OSError:
            return False
        if "netscape http cookie file" in sample.casefold():
            return True
        return any(
            len(line.split("\t")) >= 7
            for line in sample.splitlines()
            if line and not line.startswith("#")
        )

    @staticmethod
    def _configured_cookies_path(*, platform: str | None = None) -> str:
        """Read configured cookie path from environment or QSettings without auto-discovering browser or repo files."""
        if not platform:
            return ""
        configured = os.environ.get(
            f"YTDLP_{platform.upper()}_COOKIES_FILE", ""
        ).strip()
        if not configured:
            try:
                from PySide6.QtCore import QSettings

                configured = str(
                    QSettings("VideoDownloader", "VideoDownloaderFacebook").value(
                        f"{platform}/auth/cookies_file", "", type=str
                    )
                    or ""
                ).strip()
            except Exception:
                configured = ""
        return configured

    @classmethod
    def _find_cookies_file(cls, *, platform: str | None = None) -> str | None:
        """Return only an explicitly configured, readable, valid Netscape session file for this platform."""
        configured = cls._configured_cookies_path(platform=platform)
        if configured and cls._is_valid_netscape_cookies_file(configured):
            return str(Path(configured).resolve())
        return None

    def _resolve_cookie_state(self, *, platform: str | None = None) -> dict[str, object]:
        """Inspect cookie configuration and file validity; honours test patches on _find_cookies_file."""
        raw_configured = self._configured_cookies_path(platform=platform)
        resolved = self._find_cookies_file(platform=platform)
        if resolved:
            found = Path(resolved).is_file()
            valid = self._is_valid_netscape_cookies_file(resolved)
            if not valid:
                return {
                    "cookies_configured": True,
                    "cookies_file_found": found,
                    "cookies_valid": False,
                    "cookies_file": None,
                }
            return {
                "cookies_configured": True,
                "cookies_file_found": True,
                "cookies_valid": True,
                "cookies_file": str(Path(resolved).resolve()),
            }
        if raw_configured:
            found = Path(raw_configured).is_file()
            valid = found and self._is_valid_netscape_cookies_file(raw_configured)
            return {
                "cookies_configured": True,
                "cookies_file_found": found,
                "cookies_valid": valid,
                "cookies_file": None,
            }
        return {
            "cookies_configured": False,
            "cookies_file_found": False,
            "cookies_valid": False,
            "cookies_file": None,
        }

    @staticmethod
    def _yt_dlp_version() -> str:
        try:
            from yt_dlp.version import __version__
            return __version__
        except ImportError:
            return "unknown"

    @staticmethod
    def _find_javascript_runtime() -> str | None:
        configured = os.environ.get("YTDLP_JS_RUNTIME", "").strip()
        if configured and Path(configured).is_file():
            return configured
        return shutil.which("node")

    @staticmethod
    def _is_facebook_extractor_failure(output: str) -> bool:
        normalized = output.casefold()
        return any(
            marker in normalized
            for marker in ("cannot parse data", "no video formats found")
        )

    @staticmethod
    def _requires_facebook_authentication(output: str) -> bool:
        normalized = output.casefold()
        return any(
            marker in normalized
            for marker in (
                "login required",
                "log in to confirm",
                "sign in to confirm",
                "please sign in",
                "private video",
                "granted you access",
                "cookies are needed",
            )
        )

    @staticmethod
    def _facebook_redirects_to_login(url: str) -> bool:
        """Recognize Facebook's anonymous login wall without using cookies."""
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=30) as response:
                final_url = response.geturl()
        except Exception:
            return False
        parts = urlsplit(final_url)
        return (parts.hostname or "").casefold().endswith("facebook.com") and parts.path.rstrip("/").casefold() == "/login"

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
            "external_video_id": provider.video_id if provider else payload.get("id"),
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
