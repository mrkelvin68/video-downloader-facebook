"""Phase 3.1 download engine tests."""
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from requests.exceptions import ConnectionError as RequestsConnectionError

from app.database.connection import Database
from app.services.download_service import (
    DownloadError,
    DownloadCancelled,
    DownloadService,
    classify_download_error,
)
from app.services.project_service import ProjectService
from app.services.video_service import VideoService


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "db.sqlite3")
        self.project = ProjectService(self.db).create(
            "Test videos", "episode", str(self.root / "project")
        )
        VideoService(self.db).import_csv(
            self.project,
            "stt,ten_video,url\n1,Tập 1,https://www.facebook.com/watch/?v=1234\n",
        )
        self.video = self.db.list_videos(self.project)[0]

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_download_errors_are_classified_for_retry_policy(self):
        cases = (
            ("Connection reset by peer", "network", True),
            ("The operation timed out", "timeout", True),
            ("Login required", "authentication", False),
            ("This video is not available", "unavailable", False),
            ("ERROR: [facebook] 28755749490762508: Cannot parse data", "FACEBOOK_EXTRACTOR_FAILED", False),
            ("HTTP Error 404: Not Found", "unavailable", False),
            ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "forbidden", False),
            ("HTTP Error 503: Service Unavailable", "network", True),
            ("Unsupported URL", "unsupported", False),
            ("ffmpeg not found", "ffmpeg", False),
            ("No module named yt_dlp", "environment", False),
        )
        for message, category, retryable in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    classify_download_error(message), (category, retryable)
                )

    def test_download_timeout_is_a_retryable_download_error(self):
        def runner(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("yt-dlp", 1800)

        with self.assertRaises(DownloadError) as error:
            DownloadService(self.db, runner=runner).download(
                self.project, self.video["id"]
            )
        self.assertEqual(error.exception.category, "timeout")
        self.assertTrue(error.exception.retryable)
        self.assertIn("quá thời gian", str(error.exception))

    def test_progress_runner_terminates_process_when_cancelled(self):
        cancel_event = threading.Event()
        timer = threading.Timer(0.2, cancel_event.set)
        timer.start()
        try:
            with self.assertRaises(DownloadCancelled):
                DownloadService._run_with_progress(
                    [sys.executable, "-c", "import time; time.sleep(20)"],
                    lambda *_args: None,
                    cancel_event,
                )
        finally:
            timer.cancel()

    def test_ffmpeg_unavailable_is_reported_as_permanent_media_error(self):
        runner = lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stderr="ERROR: ffmpeg not found", stdout=""
        )
        service = DownloadService(self.db, runner=runner)
        with patch.object(service, "_find_ffmpeg", return_value=None):
            with self.assertRaises(DownloadError) as error:
                service.download(self.project, self.video["id"])
        self.assertEqual(error.exception.category, "ffmpeg")
        self.assertFalse(error.exception.retryable)

    def test_ffmpeg_resolution_network_error_is_retryable(self):
        with patch(
            "static_ffmpeg.run.get_or_fetch_platform_executables_else_raise",
            side_effect=RequestsConnectionError("Connection reset by peer"),
        ):
            with self.assertRaises(DownloadError) as error:
                DownloadService._find_static_ffmpeg()

        self.assertEqual(error.exception.category, "network")
        self.assertTrue(error.exception.retryable)

    def _runner(self, command, **kwargs):
        if "-show_streams" in command:
            self._probe_command = command

            return SimpleNamespace(
                returncode=0,
                stderr="",
                stdout='{"streams":[{"codec_type":"video"}],"format":{"duration":"1.0"}}',
            )
        self._download_command = command
        template = command[command.index("-o") + 1]
        output = Path(template.replace("%(ext)s", "mp4"))
        output.write_bytes(b"mock mp4")
        output.with_name(self.video["video_code"] + ".jpg").write_bytes(b"thumbnail")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    def test_success_persists_paths_and_uses_safe_ytdlp_options(self):
        output, thumb = DownloadService(self.db, runner=self._runner).download(
            self.project, self.video["id"]
        )
        self.assertEqual(output.name, "episode-1.mp4")
        self.assertEqual(thumb.name, "thumbnail.jpg")
        self.assertIn("--no-playlist", self._download_command)
        row = self.db.list_videos(self.project)[0]
        self.assertEqual(row["download_status"], "completed")
        self.assertEqual(row["local_video_path"], str(output))
        self.assertEqual(row["thumbnail_path"], str(thumb))
        with self.assertRaisesRegex(DownloadError, "đã tải"):
            DownloadService(self.db, runner=self._runner).download(self.project, self.video["id"])

    def test_failed_download_marks_failed_and_cleans_target(self):
        runner = lambda *a, **k: SimpleNamespace(returncode=1, stderr="invalid URL", stdout="")
        with self.assertRaisesRegex(DownloadError, "invalid URL"):
            DownloadService(self.db, runner=runner).download(self.project, self.video["id"])
        self.assertEqual(self.db.list_videos(self.project)[0]["download_status"], "failed")
        self.assertFalse((self.root / "project" / "episode-1").exists())

    def test_cannot_parse_data_is_permanent_extractor_failure_without_login_redirect(self):
        runner = lambda *a, **k: SimpleNamespace(
            returncode=1,
            stderr="ERROR: [facebook] 28755749490762508: Cannot parse data",
            stdout="",
        )
        service = DownloadService(self.db, runner=runner)
        with patch.object(service, "_facebook_redirects_to_login", return_value=False):
            with self.assertRaises(DownloadError) as error:
                service.download(self.project, self.video["id"])
        self.assertEqual(error.exception.category, "FACEBOOK_EXTRACTOR_FAILED")
        self.assertFalse(error.exception.retryable)

    def test_cannot_parse_data_with_login_redirect_reports_auth_without_retry(self):
        calls = []

        def runner(command, **kwargs):
            if "-show_streams" in command:
                return SimpleNamespace(
                    returncode=0,
                    stderr="",
                    stdout='{"streams":[{"codec_type":"video"}],"format":{"duration":"1.0"}}',
                )
            calls.append(list(command))
            return SimpleNamespace(
                returncode=1,
                stderr="ERROR: [facebook] 28755749490762508: Cannot parse data",
                stdout="",
            )

        service = DownloadService(self.db, runner=runner)
        with patch.object(service, "_facebook_redirects_to_login", return_value=True):
            with self.assertRaises(DownloadError) as error:
                service.download(self.project, self.video["id"])
        self.assertEqual(error.exception.category, "FACEBOOK_AUTH_REQUIRED")
        self.assertFalse(error.exception.retryable)
        self.assertEqual(len(calls), 1)

    def test_facebook_auth_failure_does_not_read_browser_cookies(self):
        calls = []

        def runner(command, **kwargs):
            if "-show_streams" in command:
                return SimpleNamespace(
                    returncode=0, stderr="",
                    stdout='{"streams":[{"codec_type":"video"}],"format":{"duration":"1.0"}}',
                )
            calls.append(list(command))
            return SimpleNamespace(
                returncode=1,
                stderr="ERROR: [facebook] 28755749490762508: Login required",
                stdout="",
            )

        service = DownloadService(self.db, runner=runner)
        with patch.object(service, "_find_cookies_file", return_value=None):
            with self.assertRaises(DownloadError) as error:
                service.download(self.project, self.video["id"])

        self.assertEqual(error.exception.category, "FACEBOOK_AUTH_REQUIRED")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("--cookies-from-browser", calls[0])

    def test_facebook_valid_cookies_are_sent_on_first_attempt(self):
        cookies_path = self.root / "cookies.txt"
        cookies_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        service = DownloadService(self.db, runner=self._runner)
        with patch.object(service, "_find_cookies_file", return_value=str(cookies_path)):
            service.download(self.project, self.video["id"])
        self.assertIn("--cookies", self._download_command)
        cookie_idx = self._download_command.index("--cookies")
        self.assertEqual(self._download_command[cookie_idx + 1], str(cookies_path.resolve()))

    def test_youtube_does_not_auto_discover_repository_cookie_file(self):
        with patch.dict("os.environ", {"YTDLP_YOUTUBE_COOKIES_FILE": "", "YTDLP_COOKIES_FILE": ""}, clear=False):
            configured = DownloadService._find_cookies_file(platform="youtube")
            self.assertNotEqual(configured, str((Path.cwd() / "cookies.txt").resolve()))

    def test_youtube_download_enables_available_javascript_runtime(self):
        service = DownloadService(self.db, runner=self._runner)
        self.db.connection.execute(
            "UPDATE videos SET source_url=? WHERE id=?",
            ("https://www.youtube.com/watch?v=NhWe4z6TiyI&list=PLUzgAhnfdKQU", self.video["id"]),
        )
        self.db.connection.commit()
        with patch.object(service, "_find_javascript_runtime", return_value="C:/node.exe"):
            service.download(self.project, self.video["id"])
        runtime_index = self._download_command.index("--js-runtimes")
        self.assertEqual(self._download_command[runtime_index + 1], "node:C:/node.exe")
        remote_index = self._download_command.index("--remote-components")
        self.assertEqual(self._download_command[remote_index + 1], "ejs:github")
        self.assertIn("--no-playlist", self._download_command)
        self.assertEqual(self._download_command[-1], "https://www.youtube.com/watch?v=NhWe4z6TiyI")

    def test_youtube_auth_error_is_not_reported_as_facebook_auth(self):
        self.db.connection.execute(
            "UPDATE videos SET source_url=? WHERE id=?",
            ("https://www.youtube.com/watch?v=NhWe4z6TiyI", self.video["id"]),
        )
        self.db.connection.commit()
        runner = lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="ERROR: [youtube] Sign in to confirm you're not a bot",
            stdout="",
        )
        with self.assertRaises(DownloadError) as raised:
            DownloadService(self.db, runner=runner).download(self.project, self.video["id"])
        self.assertEqual(raised.exception.category, "authentication")
        self.assertIn("YouTube", str(raised.exception))
        self.assertNotIn("Facebook", str(raised.exception))

    def test_youtube_http_403_is_distinct_from_authentication(self):
        self.db.connection.execute(
            "UPDATE videos SET source_url=? WHERE id=?",
            ("https://www.youtube.com/watch?v=NhWe4z6TiyI", self.video["id"]),
        )
        self.db.connection.commit()
        runner = lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="ERROR: unable to download video data: HTTP Error 403: Forbidden",
            stdout="",
        )
        with self.assertRaises(DownloadError) as raised:
            DownloadService(self.db, runner=runner).download(self.project, self.video["id"])
        self.assertEqual(raised.exception.category, "forbidden")
        self.assertNotEqual(raised.exception.category, "authentication")

    def test_youtube_valid_cookies_passed_on_first_attempt(self):
        cookies_path = self.root / "yt_cookies.txt"
        cookies_path.write_text(
            "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tsecret_sid_value\n",
            encoding="utf-8",
        )
        self.db.connection.execute(
            "UPDATE videos SET source_url=? WHERE id=?",
            ("https://www.youtube.com/watch?v=iwYbwguU9Ak", self.video["id"]),
        )
        self.db.connection.commit()
        calls: list[list[str]] = []

        def runner(command, **kwargs):
            if "-show_streams" in command:
                return SimpleNamespace(
                    returncode=0,
                    stderr="",
                    stdout='{"streams":[{"codec_type":"video"}],"format":{"duration":"1.0"}}',
                )
            calls.append(list(command))
            template = command[command.index("-o") + 1]
            output = Path(template.replace("%(ext)s", "mp4"))
            output.write_bytes(b"mock mp4")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        service = DownloadService(self.db, runner=runner)
        with (
            patch.dict("os.environ", {"YTDLP_YOUTUBE_COOKIES_FILE": str(cookies_path)}, clear=False),
            patch("app.services.download_service.log_download") as mock_log,
        ):
            service.download(self.project, self.video["id"])

        self.assertEqual(len(calls), 1)
        self.assertIn("--cookies", calls[0])
        cookie_idx = calls[0].index("--cookies")
        self.assertEqual(calls[0][cookie_idx + 1], str(cookies_path.resolve()))
        logged_messages = "\n".join(str(c.args[0]) for c in mock_log.call_args_list if c.args)
        self.assertIn("cookies_configured=True", logged_messages)
        self.assertIn("cookies_file_found=True", logged_messages)
        self.assertIn("cookies_applied=True", logged_messages)
        self.assertIn("attempt_number=1", logged_messages)
        self.assertNotIn("secret_sid_value", logged_messages)

    def test_youtube_missing_and_invalid_cookie_files_are_not_applied(self):
        self.db.connection.execute(
            "UPDATE videos SET source_url=? WHERE id=?",
            ("https://www.youtube.com/watch?v=iwYbwguU9Ak", self.video["id"]),
        )
        self.db.connection.commit()

        # 1. Missing cookie file
        missing_path = self.root / "nonexistent_cookies.txt"
        calls_missing: list[list[str]] = []

        def runner_fail(command, **kwargs):
            calls_missing.append(list(command))
            return SimpleNamespace(
                returncode=1,
                stderr="ERROR: [youtube] iwYbwguU9Ak: Private video. If the owner of this video has granted you access, please sign in.",
                stdout="",
            )

        with patch.dict("os.environ", {"YTDLP_YOUTUBE_COOKIES_FILE": str(missing_path)}, clear=False):
            with self.assertRaises(DownloadError) as err_missing:
                DownloadService(self.db, runner=runner_fail).download(self.project, self.video["id"])

        self.assertEqual(len(calls_missing), 1)
        self.assertNotIn("--cookies", calls_missing[0])
        self.assertEqual(err_missing.exception.category, "authentication")
        self.assertIn("không tồn tại", str(err_missing.exception))

        # 2. Invalid (non-Netscape) cookie file
        invalid_path = self.root / "invalid_cookies.txt"
        invalid_path.write_text("{\"not\": \"netscape format\"}", encoding="utf-8")
        calls_invalid: list[list[str]] = []

        def runner_fail_invalid(command, **kwargs):
            calls_invalid.append(list(command))
            return SimpleNamespace(
                returncode=1,
                stderr="ERROR: [youtube] iwYbwguU9Ak: Private video. If the owner of this video has granted you access, please sign in.",
                stdout="",
            )

        with patch.dict("os.environ", {"YTDLP_YOUTUBE_COOKIES_FILE": str(invalid_path)}, clear=False):
            with self.assertRaises(DownloadError) as err_invalid:
                DownloadService(self.db, runner=runner_fail_invalid).download(self.project, self.video["id"])

        self.assertEqual(len(calls_invalid), 1)
        self.assertNotIn("--cookies", calls_invalid[0])
        self.assertEqual(err_invalid.exception.category, "authentication")
        self.assertIn("Netscape", str(err_invalid.exception))

    def test_youtube_private_video_without_cookies_vs_with_cookies_no_permission_and_http_403(self):
        self.db.connection.execute(
            "UPDATE videos SET source_url=? WHERE id=?",
            ("https://www.youtube.com/watch?v=iwYbwguU9Ak", self.video["id"]),
        )
        self.db.connection.commit()

        private_stderr = (
            "ERROR: [youtube] iwYbwguU9Ak: Private video. "
            "If the owner of this video has granted you access, please sign in."
        )
        runner_private = lambda *a, **k: SimpleNamespace(returncode=1, stderr=private_stderr, stdout="")

        # 1. Private video without configured cookies -> authentication required
        with (
            patch.dict("os.environ", {"YTDLP_YOUTUBE_COOKIES_FILE": ""}, clear=False),
            patch.object(DownloadService, "_configured_cookies_path", return_value=""),
        ):
            with self.assertRaises(DownloadError) as err_no_cookies:
                DownloadService(self.db, runner=runner_private).download(self.project, self.video["id"])
        self.assertEqual(err_no_cookies.exception.category, "authentication")
        self.assertFalse(err_no_cookies.exception.retryable)
        self.assertIn("Riêng tư (Private)", str(err_no_cookies.exception))

        # 2. Private video WITH valid cookies applied on first attempt, but account lacks access permission
        cookies_path = self.root / "valid_yt_cookies.txt"
        cookies_path.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tval\n", encoding="utf-8")
        calls_priv: list[list[str]] = []

        def runner_private_recorded(command, **kwargs):
            calls_priv.append(list(command))
            return SimpleNamespace(returncode=1, stderr=private_stderr, stdout="")

        with patch.dict("os.environ", {"YTDLP_YOUTUBE_COOKIES_FILE": str(cookies_path)}, clear=False):
            with self.assertRaises(DownloadError) as err_no_perm:
                DownloadService(self.db, runner=runner_private_recorded).download(self.project, self.video["id"])
        # Applied on attempt 1, not redundantly retried on attempt 2
        self.assertEqual(len(calls_priv), 1)
        self.assertIn("--cookies", calls_priv[0])
        self.assertEqual(err_no_perm.exception.category, "private_access_denied")
        self.assertFalse(err_no_perm.exception.retryable)
        self.assertIn("chưa được chủ sở hữu cấp quyền xem", str(err_no_perm.exception))

        # 3. HTTP 403 after authenticated attempt
        calls_403: list[list[str]] = []

        def runner_403(command, **kwargs):
            calls_403.append(list(command))
            return SimpleNamespace(
                returncode=1,
                stderr="ERROR: unable to download video data: HTTP Error 403: Forbidden",
                stdout="",
            )

        with patch.dict("os.environ", {"YTDLP_YOUTUBE_COOKIES_FILE": str(cookies_path)}, clear=False):
            with self.assertRaises(DownloadError) as err_403:
                DownloadService(self.db, runner=runner_403).download(self.project, self.video["id"])
        self.assertEqual(len(calls_403), 1)
        self.assertIn("--cookies", calls_403[0])
        self.assertEqual(err_403.exception.category, "forbidden")
        self.assertFalse(err_403.exception.retryable)

    def test_download_worker_uses_same_youtube_cookie_settings_and_keeps_fb_instagram_behavior(self):
        from app.services.download_queue_service import DownloadQueueService
        from app.ui.download_worker import DownloadWorker

        cookies_path = self.root / "worker_yt_cookies.txt"
        cookies_path.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tval\n", encoding="utf-8")

        # Add YouTube and Instagram videos to test worker + platform behavior
        VideoService(self.db).import_csv(
            self.project,
            "stt,ten_video,url\n"
            "1,Tập 1,https://www.facebook.com/watch/?v=1234\n"
            "2,YT Video,https://www.youtube.com/watch?v=iwYbwguU9Ak\n"
            "3,IG Reel,https://www.instagram.com/reel/ABC123xyz/\n",
            replace_confirmed=True,
        )
        videos = {v["video_code"]: v for v in self.db.list_videos(self.project)}
        yt_video = videos["episode-2"]
        ig_video = videos["episode-3"]

        queue_service = DownloadQueueService(self.db)
        job_ids = queue_service.enqueue(self.project, [yt_video["id"]], run_now=True)
        self.assertEqual(len(job_ids), 1)

        recorded_commands: list[list[str]] = []

        def fake_run_with_progress(command, progress_callback, cancel_event=None):
            recorded_commands.append(list(command))
            template = command[command.index("-o") + 1]
            output = Path(template.replace("%(ext)s", "mp4"))
            output.write_bytes(b"mock mp4")
            progress_callback(100.0, 1024, 1024, 512.0, 0)
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with (
            patch.dict("os.environ", {"YTDLP_YOUTUBE_COOKIES_FILE": str(cookies_path)}, clear=False),
            patch.object(DownloadService, "_run_with_progress", side_effect=fake_run_with_progress),
            patch.object(DownloadService, "_validate_media", return_value=None),
        ):
            worker = DownloadWorker(str(self.root / "db.sqlite3"), job_ids[0])
            worker.run()

        self.assertEqual(len(recorded_commands), 1)
        self.assertIn("--cookies", recorded_commands[0])
        self.assertNotIn("--cookies-from-browser", recorded_commands[0])

        # Verify Instagram public download does NOT pass --cookies on first attempt
        ig_commands: list[list[str]] = []

        def ig_runner(command, **kwargs):
            if "-show_streams" in command:
                return SimpleNamespace(
                    returncode=0,
                    stderr="",
                    stdout='{"streams":[{"codec_type":"video"}],"format":{"duration":"1.0"}}',
                )
            ig_commands.append(list(command))
            template = command[command.index("-o") + 1]
            Path(template.replace("%(ext)s", "mp4")).write_bytes(b"mock mp4")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        ig_cookies = self.root / "ig_cookies.txt"
        ig_cookies.write_text("# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tval\n", encoding="utf-8")
        with patch.dict("os.environ", {"YTDLP_INSTAGRAM_COOKIES_FILE": str(ig_cookies)}, clear=False):
            DownloadService(self.db, runner=ig_runner).download(self.project, ig_video["id"])
        self.assertEqual(len(ig_commands), 1)
        self.assertNotIn("--cookies", ig_commands[0])

    def test_ffprobe_failure_marks_failed(self):
        def runner(command, **kwargs):
            if "-show_streams" in command:
                return SimpleNamespace(returncode=1, stderr="bad media", stdout="")
            output = Path(command[command.index("-o") + 1].replace("%(ext)s", "mp4"))
            output.write_bytes(b"not media")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        with self.assertRaisesRegex(DownloadError, "FFprobe thất bại"):
            DownloadService(self.db, runner=runner).download(self.project, self.video["id"])
        self.assertEqual(self.db.list_videos(self.project)[0]["download_status"], "failed")

    def test_failed_status_write_is_reported(self):
        service = DownloadService(
            self.db,
            runner=lambda *a, **k: SimpleNamespace(
                returncode=1, stderr="invalid URL", stdout=""
            ),
        )
        original_set_status = service._set_status

        def fail_failed_status(project_id, video_id, status, *args, **kwargs):
            if status == "failed":
                raise OSError("database write failed")
            return original_set_status(project_id, video_id, status, *args, **kwargs)

        with patch.object(service, "_set_status", side_effect=fail_failed_status):
            with self.assertRaises(DownloadError) as error:
                service.download(self.project, self.video["id"])
        self.assertIn("invalid URL", str(error.exception))
        self.assertIn("database write failed", str(error.exception))
        self.assertFalse(error.exception.retryable)
        self.assertEqual(
            self.db.list_videos(self.project)[0]["download_status"], "downloading"
        )

    def test_existing_folder_and_wrong_project_preserve_data(self):
        target = self.root / "project" / "episode-1"
        target.mkdir()
        (target / "keep.txt").write_text("SAFE", encoding="utf-8")
        with self.assertRaisesRegex(DownloadError, "không ghi đè"):
            DownloadService(self.db).download(self.project, self.video["id"])
        with self.assertRaises(ValueError):
            DownloadService(self.db).download("wrong-project", self.video["id"])
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "SAFE")
        self.assertEqual(self.db.list_videos(self.project)[0]["download_status"], "pending")

    def test_interrupted_status_recovery_is_project_scoped(self):
        self.db.connection.execute(
            "UPDATE videos SET download_status='downloading' WHERE id=?", (self.video["id"],)
        )
        self.db.connection.commit()
        self.assertEqual(DownloadService(self.db).recover_interrupted(self.project), 1)
        self.assertEqual(self.db.list_videos(self.project)[0]["download_status"], "failed")

    def test_real_media_integration_and_restart(self):
        import subprocess
        ffmpeg = DownloadService._find_ffmpeg()
        ffprobe = DownloadService._find_ffprobe()
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg/FFprobe không có sẵn để chạy integration test")

        def ytdlp_runner(command, **kwargs):
            if "-show_streams" in command:
                return subprocess.run(command, **kwargs)
            template = command[command.index("-o") + 1]
            mp4_out = Path(template.replace("%(ext)s", "mp4"))
            cmd = [
                ffmpeg, "-y",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=1",
                "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                "-c:v", "libx264", "-c:a", "aac",
                str(mp4_out),
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            thumb_out = mp4_out.with_name(self.video["video_code"] + ".jpg")
            thumb_out.write_bytes(b"image")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        service = DownloadService(self.db, runner=ytdlp_runner)
        out, thumb = service.download(self.project, self.video["id"])

        self.assertTrue(out.is_file())
        self.assertGreater(out.stat().st_size, 0)
        self.assertIsNotNone(thumb)
        self.assertTrue(thumb.is_file())
        self.assertEqual(thumb.name, "thumbnail.jpg")

        service._validate_media(out)

        row = self.db.list_videos(self.project)[0]
        self.assertEqual(row["download_status"], "completed")
        self.assertEqual(row["local_video_path"], str(out))
        self.assertEqual(row["thumbnail_path"], str(thumb))

        restarted_db = Database(self.root / "db.sqlite3")
        restarted_row = restarted_db.list_videos(self.project)[0]
        self.assertEqual(restarted_row["download_status"], "completed")
        self.assertEqual(restarted_row["local_video_path"], str(out))
        restarted_db.close()



if __name__ == "__main__":
    unittest.main()
