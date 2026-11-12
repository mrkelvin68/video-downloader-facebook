import unittest
from pathlib import Path
import tempfile

from app.database.connection import Database
from app.services.auto_comment_service import (
    AutoCommentService, AutoCommentValidationError, CommentItem, parse_comment_csv, select_comments,
)
from app.services.facebook_publishing_service import FacebookPublishingService
from app.services.project_service import ProjectService
from app.services.video_service import VideoService


class AutoCommentPhase1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite3")
        self.project_id = ProjectService(self.database).create("Project", "p", str(Path(self.temp.name) / "videos"))
        VideoService(self.database).import_csv(self.project_id, "stt,title,url\n1,Title,https://www.facebook.com/watch/?v=1")
        self.video_id = self.database.list_videos(self.project_id)[0]["id"]
        FacebookPublishingService(self.database).save_pages(self.project_id, [{"id": "page-1", "name": "Page 1"}])

    def tearDown(self):
        self.database.close(); self.temp.cleanup()

    def test_csv_utf8_bom_unicode_multiline_and_duplicates(self):
        result = parse_comment_csv(b'\xef\xbb\xbfid,comment,enabled\n1,"Xin chao, Viet Nam",1\n2,"Dong 1\nDong 2",0\n3,"Xin chao, Viet Nam",1\n')
        self.assertEqual([item.comment_text for item in result.items][1], "Dong 1\nDong 2")
        self.assertFalse(result.items[1].enabled)
        self.assertTrue(result.warnings)

    def test_csv_validation(self):
        with self.assertRaises(AutoCommentValidationError): parse_comment_csv("id,comment\n1,\n")
        with self.assertRaises(AutoCommentValidationError): parse_comment_csv("id,comment\n1,a\n1,b\n")
        with self.assertRaises(AutoCommentValidationError): parse_comment_csv("text\na\n")

    def test_selection_random_and_sequential_are_distinct(self):
        items = [CommentItem("a", True, "2", 2), CommentItem("b", True, "1", 1), CommentItem("c", True, "3", 3), CommentItem("off", False, "4", 0)]
        self.assertEqual(select_comments(items, 1, "sequential"), ["b"])
        self.assertEqual(set(select_comments(items, 3, "random")), {"a", "b", "c"})
        with self.assertRaises(AutoCommentValidationError): select_comments(items[:2], 3, "random")

    def test_library_and_preview_persist_and_local_posts_are_page_scoped(self):
        service = AutoCommentService(self.database)
        library_id = service.save_library("Thu vien", [CommentItem("Binh luan", True, "1", 0)])
        self.assertEqual(service.load_library(library_id)[0].comment_text, "Binh luan")
        with self.database.connection:
            self.database.connection.execute(
                """INSERT INTO facebook_publishing_jobs
                   (id,project_id,video_id,page_id,local_file_path,status,published_post_id,created_at,updated_at)
                   VALUES ('job',?,?,?,?,?,'post-1','2026-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00')""",
                (self.project_id, self.video_id, "page-1", "x.mp4", "PUBLISHED"),
            )
        posts = service.local_posts("page-1", "2026-01-02", "2026-01-02", "all")
        self.assertEqual(posts[0]["post_id"], "post-1")
        campaign_id = service.save_preview({"name": "Draft", "page_id": "page-1", "start_date": "2026-01-02", "end_date": "2026-01-02", "content_type": "Tất cả", "selection_mode": "sequential", "count": 1, "prevent_duplicates": True}, posts, {"post-1": ["Binh luan"]})
        self.assertEqual(self.database.connection.execute("SELECT COUNT(*) FROM auto_comment_preview_items WHERE campaign_id=?", (campaign_id,)).fetchone()[0], 1)
        campaign, preview = service.load_campaign_preview(campaign_id)
        self.assertEqual(campaign["status"], "PREVIEW_READY")
        self.assertEqual(preview[0]["comment_text"], "Binh luan")


if __name__ == "__main__":
    unittest.main()
