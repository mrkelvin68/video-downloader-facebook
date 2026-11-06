import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.core.validators import normalize_video_url, validate_supported_video_url
from app.database.connection import Database
from app.services.csv_service import parse_video_csv
from app.services.download_service import DownloadService
from app.services.project_service import ProjectService
from app.services.video_download_provider import VideoPlatform, provider_for_url
from app.services.video_service import VideoService


class VideoPlatformImportTests(unittest.TestCase):
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

    def test_rejects_invalid_or_unsupported_urls(self):
        for url in (
            "https://www.youtube.com/watch",
            "https://www.instagram.com/stories/example/",
            "https://example.com/video",
            "not a url",
        ):
            with self.subTest(url=url):
                self.assertFalse(validate_supported_video_url(url))

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
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
