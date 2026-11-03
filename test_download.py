"""Phase 3 tests: no real Facebook traffic; yt-dlp subprocess is stubbed."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.database.connection import Database
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.services.download_service import DownloadService, DownloadError


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
            "stt,ten_video,url\n1,Tập 1,https://www.facebook.com/watch/?v=1234\n"
        )
        self.video = self.db.list_videos(self.project)[0]

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _runner(self, command, **kwargs):
        template = command[command.index("-o") + 1]
        code = self.video["video_code"]
        output = Path(template.replace("%(ext)s", "mp4"))
        output.write_bytes(b"mock mp4 for workflow test")
        output.with_name(code + ".jpg").write_bytes(b"mock thumbnail")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    @patch("app.services.download_service.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg")
    def test_success_persists_video_and_thumbnail(self, _ffmpeg):
        output, thumb = DownloadService(self.db, runner=self._runner).download(
            self.project, self.video["id"]
        )
        self.assertEqual(output.name, "episode-1.mp4")
        self.assertTrue(output.exists())
        self.assertTrue(thumb.exists())
        row = self.db.list_videos(self.project)[0]
        self.assertEqual(row["download_status"], "completed")
        self.assertEqual(row["local_video_path"], str(output))
        self.assertEqual(row["thumbnail_path"], str(thumb))
        with self.assertRaises(DownloadError):
            DownloadService(self.db, runner=self._runner).download(self.project, self.video["id"])

    @patch("app.services.download_service.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg")
    def test_failed_download_marks_failed_without_final_folder(self, _ffmpeg):
        runner = lambda *a, **k: SimpleNamespace(returncode=1, stderr="broken", stdout="")
        with self.assertRaisesRegex(DownloadError, "broken"):
            DownloadService(self.db, runner=runner).download(self.project, self.video["id"])
        self.assertEqual(self.db.list_videos(self.project)[0]["download_status"], "failed")
        self.assertFalse((self.root / "project" / "episode-1").exists())

    def test_existing_folder_never_overwritten(self):
        destination = self.root / "project" / "episode-1"
        destination.mkdir()
        (destination / "keep.txt").write_text("SAFE", encoding="utf-8")
        with self.assertRaisesRegex(DownloadError, "không ghi đè"):
            DownloadService(self.db).download(self.project, self.video["id"])
        self.assertEqual((destination / "keep.txt").read_text(), "SAFE")
        self.assertEqual(self.db.list_videos(self.project)[0]["download_status"], "pending")

    def test_wrong_project_and_interrupted_recovery(self):
        with self.assertRaises(ValueError):
            DownloadService(self.db).download("wrong-project", self.video["id"])
        self.db.connection.execute(
            "UPDATE videos SET download_status='downloading' WHERE id=?", (self.video["id"],)
        )
        self.db.connection.commit()
        self.assertEqual(DownloadService(self.db).recover_interrupted(self.project), 1)
        self.assertEqual(self.db.list_videos(self.project)[0]["download_status"], "failed")


if __name__ == "__main__":
    unittest.main()
