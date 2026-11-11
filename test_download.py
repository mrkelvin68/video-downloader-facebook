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
            ("ERROR: [facebook] 28755749490762508: Cannot parse data", "extractor", True),
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

    def test_cannot_parse_data_is_retryable_extractor_failure(self):
        runner = lambda *a, **k: SimpleNamespace(
            returncode=1,
            stderr="ERROR: [facebook] 28755749490762508: Cannot parse data",
            stdout="",
        )
        service = DownloadService(self.db, runner=runner)
        with patch.object(service, "_resolve_facebook_mirror_url", return_value=None):
            with self.assertRaises(DownloadError) as error:
                service.download(self.project, self.video["id"])
        self.assertEqual(error.exception.category, "extractor")
        self.assertTrue(error.exception.retryable)

    def test_cannot_parse_data_retries_the_original_facebook_reel(self):
        calls = []

        def runner(command, **kwargs):
            if "-show_streams" in command:
                return SimpleNamespace(
                    returncode=0,
                    stderr="",
                    stdout='{"streams":[{"codec_type":"video"}],"format":{"duration":"1.0"}}',
                )
            calls.append(list(command))
            if len(calls) == 1:
                return SimpleNamespace(
                    returncode=1,
                    stderr="ERROR: [facebook] 28755749490762508: Cannot parse data",
                    stdout="",
                )
            template = command[command.index("-o") + 1]
            output = Path(template.replace("%(ext)s", "mp4"))
            output.write_bytes(b"mock mp4")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        service = DownloadService(self.db, runner=runner)
        out, _ = service.download(self.project, self.video["id"])
        self.assertTrue(out.is_file())
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][-1], "https://facebook.com/watch/?v=1234")

    def test_facebook_auth_failure_retries_with_explicitly_configured_browser_cookies(self):
        calls = []

        def runner(command, **kwargs):
            if "-show_streams" in command:
                return SimpleNamespace(
                    returncode=0, stderr="",
                    stdout='{"streams":[{"codec_type":"video"}],"format":{"duration":"1.0"}}',
                )
            calls.append(list(command))
            if len(calls) == 1:
                return SimpleNamespace(
                    returncode=1,
                    stderr="ERROR: [facebook] 28755749490762508: Login required",
                    stdout="",
                )
            template = command[command.index("-o") + 1]
            Path(template.replace("%(ext)s", "mp4")).write_bytes(b"mock mp4")
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        service = DownloadService(self.db, runner=runner)
        with patch.object(service, "_find_cookies_file", return_value=None), patch.object(
            service, "_find_browser_cookie_sources", return_value=["chrome"]
        ):
            output, _ = service.download(self.project, self.video["id"])

        self.assertTrue(output.is_file())
        self.assertEqual(len(calls), 2)
        cookie_option = calls[1].index("--cookies-from-browser")
        self.assertEqual(calls[1][cookie_option + 1], "chrome")

    def test_cookies_file_is_not_sent_for_public_download(self):
        cookies_path = self.root / "cookies.txt"
        cookies_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        service = DownloadService(self.db, runner=self._runner)
        with patch.object(service, "_find_cookies_file", return_value=str(cookies_path)):
            service.download(self.project, self.video["id"])
        self.assertNotIn("--cookies", self._download_command)

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
