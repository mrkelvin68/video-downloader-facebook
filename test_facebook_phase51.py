import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.connection import Database
from app.services.facebook_connection_service import FacebookConnectionService
from app.services.facebook_publishing_service import FacebookPublishingService
from app.services.project_service import ProjectService
from app.services.video_service import VideoService


class _Client:
    def __init__(self, token):
        self.token = token

    def validate_and_list_pages(self):
        return [{"id": "page-1", "name": "Page 1", "url": "https://facebook.com/page-1",
                 "permissions": ["pages_show_list", "pages_read_engagement", "pages_manage_posts"],
                 "access_token": "page-secret"}]


class FacebookPhase51Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite3")
        self.project_id = ProjectService(self.database).create(
            "Project", "p", str(Path(self.temp.name) / "videos")
        )
        VideoService(self.database).import_csv(
            self.project_id, "stt,title,url\n1,Video title,https://www.facebook.com/watch/?v=1"
        )
        self.video_id = self.database.list_videos(self.project_id)[0]["id"]
        self.video_path = Path(self.temp.name) / "videos" / "video.mp4"
        self.video_path.write_bytes(b"test")
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE videos SET local_video_path=? WHERE id=?",
                (str(self.video_path), self.video_id),
            )

    def tearDown(self):
        self.database.close()
        self.temp.cleanup()

    def test_connection_pages_are_project_scoped_and_token_is_not_stored_in_database(self):
        with patch("app.services.facebook_connection_service.set_meta_token"), patch(
            "app.services.facebook_connection_service.set_page_token"
        ):
            pages = FacebookConnectionService(self.database, _Client).connect(
                self.project_id, "user-secret"
            )
        self.assertEqual(pages[0]["id"], "page-1")
        self.assertEqual(self.database.connection.execute(
            "SELECT page_name FROM facebook_pages WHERE project_id=?", (self.project_id,)
        ).fetchone()[0], "Page 1")
        schema = self.database.connection.execute("PRAGMA table_info(facebook_pages)").fetchall()
        self.assertNotIn("access_token", {row[1] for row in schema})

    def test_draft_and_preparation_persist_without_changing_video_title(self):
        service = FacebookPublishingService(self.database)
        service.save_pages(self.project_id, [{"id": "page-1", "name": "Page 1"}], "page-1")
        service.save_draft(
            self.project_id, self.video_id, page_id="page-1", facebook_title="Facebook title",
            description="Nội dung\nTiếng Việt", hashtags="#tag",
        )
        with patch.object(service, "_inspect_video", return_value={
            "format": {"duration": "2"},
            "streams": [{"codec_type": "video", "width": 1280, "height": 720}],
        }):
            self.assertEqual(service.prepare_video(self.project_id, self.video_id)["status"], "READY")
        draft = service.list_drafts(self.project_id)[0]
        self.assertEqual(draft["facebook_title"], "Facebook title")
        self.assertEqual(draft["description"], "Nội dung\nTiếng Việt")
        self.assertEqual(self.database.list_videos(self.project_id)[0]["title"], "Video title")

    def test_missing_local_file_is_reported_without_deleting_video(self):
        service = FacebookPublishingService(self.database)
        with self.database.connection:
            self.database.connection.execute("UPDATE videos SET local_video_path=NULL WHERE id=?", (self.video_id,))
        result = service.prepare_video(self.project_id, self.video_id)
        self.assertEqual(result["status"], "MISSING_FILE")
        self.assertIsNotNone(self.database.connection.execute(
            "SELECT id FROM videos WHERE id=?", (self.video_id,)
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
