import unittest
import tempfile
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.core.validators import normalize_video_url, validate_supported_video_url
from app.database.connection import Database
from app.services.csv_service import parse_video_csv
from app.services.download_service import DownloadService, classify_download_error
from app.services.project_service import ProjectService
from app.services.video_download_provider import VideoPlatform, provider_for_url
from app.services.video_service import VideoService


class VideoPlatformImportTests(unittest.TestCase):
    def test_metadata_migration_preserves_existing_video(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "legacy.sqlite3"
            legacy = sqlite3.connect(database_path)
            legacy.executescript(
                """CREATE TABLE projects (
                    uuid TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL,
                    output_dir TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE videos (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, sequence_number INTEGER NOT NULL,
                    video_code TEXT NOT NULL, title TEXT NOT NULL, source_url TEXT NOT NULL,
                    normalized_url TEXT NOT NULL, download_status TEXT NOT NULL DEFAULT 'pending',
                    local_video_path TEXT, thumbnail_path TEXT,
                    facebook_uploaded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(project_id, sequence_number), UNIQUE(project_id, normalized_url),
                    UNIQUE(project_id, video_code));
                INSERT INTO projects VALUES ('p1', 'Project', 'p', 'C:/output', '', 'now', 'now');
                INSERT INTO videos (id, project_id, sequence_number, video_code, title, source_url,
                    normalized_url, created_at, updated_at)
                    VALUES ('v1', 'p1', 1, 'p-1', 'Existing', 'https://www.facebook.com/reel/1',
                    'https://facebook.com/reel/1', 'now', 'now');"""
            )
            legacy.commit()
            legacy.close()
            database = Database(database_path)
            try:
                video = database.list_videos("p1")[0]
                self.assertEqual(video["title"], "Existing")
                self.assertIn("platform", video.keys())
                self.assertIsNone(video["platform"])
            finally:
                database.close()

    def test_recognizes_youtube_watch_short_and_shortlink(self):
        cases = {
            "https://www.youtube.com/watch?v=abc123XYZ09": "abc123XYZ09",
            "https://youtu.be/abc123XYZ09?t=3": "abc123XYZ09",
            "https://www.youtube.com/shorts/abc123XYZ09?feature=share": "abc123XYZ09",
        }
        for url, video_id in cases.items():
            with self.subTest(url=url):
                provider = provider_for_url(url)
                self.assertIsNotNone(provider)
                self.assertEqual(provider.platform, VideoPlatform.YOUTUBE)
                self.assertEqual(provider.video_id, video_id)
                self.assertEqual(
                    provider.normalized_url,
                    f"https://www.youtube.com/watch?v={video_id}",
                )

    def test_recognizes_instagram_reel_and_post_without_tracking_query(self):
        for url, expected in (
            ("https://www.instagram.com/reel/ABC123/?igsh=tracking", "reel"),
            ("https://instagram.com/p/ABC123/?utm_source=test", "p"),
        ):
            with self.subTest(url=url):
                provider = provider_for_url(url)
                self.assertIsNotNone(provider)
                self.assertEqual(provider.platform, VideoPlatform.INSTAGRAM)
                self.assertEqual(provider.normalized_url, f"https://www.instagram.com/{expected}/ABC123/")

    def test_recognizes_facebook_reel_and_watch(self):
        for url, expected_id in (
            ("https://www.facebook.com/reel/1195289147628387?tracking=1", "1195289147628387"),
            ("https://www.facebook.com/watch/?v=647537299265662&utm_source=test", "647537299265662"),
        ):
            with self.subTest(url=url):
                provider = provider_for_url(url)
                self.assertIsNotNone(provider)
                self.assertEqual(provider.platform, VideoPlatform.FACEBOOK)
                self.assertEqual(provider.video_id, expected_id)

    def test_rejects_invalid_or_unsupported_urls(self):
        for url in (
            "https://www.youtube.com/watch",
            "https://youtu.be/too-short",
            "https://www.youtube.com/shorts/not-an-id!",
            "https://www.instagram.com/stories/example/",
            "https://www.instagram.com/reel/a!bad/",
            "https://www.facebook.com/reel/bad!id/",
            "ftp://www.youtube.com/watch?v=abc123XYZ09",
            "https://example.com/video",
            "not a url",
        ):
            with self.subTest(url=url):
                self.assertFalse(validate_supported_video_url(url))

    def test_platform_access_restrictions_are_permanent(self):
        self.assertEqual(
            classify_download_error("This video is not available in your country"),
            ("blocked", False),
        )

    def test_csv_accepts_mixed_platforms_and_canonicalizes_duplicates(self):
        text = (
            "title,url\n"
            "Facebook,https://www.facebook.com/watch/?v=1\n"
            "YouTube,https://youtu.be/abc123XYZ09\n"
            "Instagram,https://www.instagram.com/reel/ABC123/?utm_source=test\n"
        )
        rows, errors = parse_video_csv(text)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            normalize_video_url("https://youtu.be/abc123XYZ09?t=1"),
            "https://www.youtube.com/watch?v=abc123XYZ09",
        )
        self.assertEqual(
            normalize_video_url("https://www.facebook.com/watch/?v=1&utm_source=test"),
            "https://facebook.com/watch/?v=1",
        )
        duplicate_rows, duplicate_errors = parse_video_csv(
            "title,url\nOne,https://youtu.be/abc123XYZ09\nTwo,https://www.youtube.com/watch?v=abc123XYZ09\n"
        )
        self.assertEqual(duplicate_rows, [])
        self.assertTrue(duplicate_errors)

    def test_youtube_download_uses_existing_queue_compatible_mp4_flow(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = Database(root / "project.sqlite3")
            try:
                project_id = ProjectService(database).create(
                    "Import test", "import", str(root / "videos")
                )
                VideoService(database).import_csv(
                    project_id,
                    "title,url\nYouTube,https://youtu.be/abc123XYZ09?t=20\n",
                )
                video = database.list_videos(project_id)[0]
                download_command: list[str] = []

                def runner(command, **_kwargs):
                    if command[0] == "ffprobe":
                        return SimpleNamespace(
                            returncode=0,
                            stderr="",
                            stdout='{"streams":[{"codec_type":"video"}],"format":{"duration":"1"}}',
                        )
                    download_command[:] = command
                    output = Path(command[command.index("-o") + 1].replace("%(ext)s", "mp4"))
                    output.write_bytes(b"mock mp4")
                    output.with_name("import-1.info.json").write_text(
                        json.dumps(
                            {
                                "id": "abc123XYZ09",
                                "channel": "Test channel",
                                "duration": 12.5,
                                "width": 1920,
                                "height": 1080,
                            }
                        ),
                        encoding="utf-8",
                    )
                    return SimpleNamespace(returncode=0, stderr="", stdout="")

                output, _thumbnail = DownloadService(
                    database, runner=runner, ffprobe=lambda: "ffprobe"
                ).download(project_id, video["id"])
                self.assertTrue(output.is_file())
                self.assertIn(
                    "https://www.youtube.com/watch?v=abc123XYZ09",
                    download_command,
                )
                self.assertEqual(
                    database.list_videos(project_id)[0]["download_status"], "completed"
                )
                saved = database.list_videos(project_id)[0]
                self.assertEqual(saved["platform"], "youtube")
                self.assertEqual(saved["external_video_id"], "abc123XYZ09")
                self.assertEqual(saved["author_name"], "Test channel")
                self.assertEqual(saved["duration_seconds"], 12.5)
                self.assertEqual(saved["resolution"], "1920x1080")
            finally:
                database.close()

    def test_failed_completion_recovers_valid_file_without_redownload(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = Database(root / "project.sqlite3")
            try:
                project_id = ProjectService(database).create(
                    "Recovery test", "recovery", str(root / "videos")
                )
                VideoService(database).import_csv(
                    project_id, "title,url\nYouTube,https://youtu.be/abc123XYZ09\n"
                )
                video = database.list_videos(project_id)[0]
                target = root / "videos" / video["video_code"]
                target.mkdir(parents=True)
                (target / f"{video['video_code']}.mp4").write_bytes(b"valid")
                with database.connection:
                    database.connection.execute(
                        "UPDATE videos SET download_status='failed' WHERE id=?", (video["id"],)
                    )
                service = DownloadService(
                    database,
                    runner=lambda *_args, **_kwargs: self.fail("must not re-download"),
                    ffprobe=lambda: "ffprobe",
                )
                service._validate_media = lambda _path: None
                output, _thumbnail = service.download(project_id, video["id"])
                self.assertTrue(output.is_file())
                self.assertEqual(
                    database.list_videos(project_id)[0]["download_status"], "completed"
                )
            finally:
                database.close()

    def test_metadata_database_failure_keeps_valid_file_recoverable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = Database(root / "project.sqlite3")
            try:
                project_id = ProjectService(database).create(
                    "Metadata failure", "metadata", str(root / "videos")
                )
                VideoService(database).import_csv(
                    project_id, "title,url\nYouTube,https://youtu.be/abc123XYZ09\n"
                )
                video = database.list_videos(project_id)[0]

                def runner(command, **_kwargs):
                    if command[0] == "ffprobe":
                        return SimpleNamespace(
                            returncode=0,
                            stderr="",
                            stdout='{"streams":[{"codec_type":"video"}],"format":{"duration":"1"}}',
                        )
                    output = Path(command[command.index("-o") + 1].replace("%(ext)s", "mp4"))
                    output.write_bytes(b"valid")
                    return SimpleNamespace(returncode=0, stderr="", stdout="")

                service = DownloadService(database, runner=runner, ffprobe=lambda: "ffprobe")
                with patch.object(
                    service, "_set_video_metadata", side_effect=sqlite3.OperationalError("metadata write failed")
                ):
                    with self.assertRaises(sqlite3.OperationalError):
                        service.download(project_id, video["id"])
                target = root / "videos" / video["video_code"] / f"{video['video_code']}.mp4"
                self.assertTrue(target.is_file())
                self.assertEqual(database.list_videos(project_id)[0]["download_status"], "failed")
                output, _thumbnail = DownloadService(
                    database, runner=runner, ffprobe=lambda: "ffprobe"
                ).download(project_id, video["id"])
                self.assertEqual(output, target)
                self.assertEqual(database.list_videos(project_id)[0]["download_status"], "completed")
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
