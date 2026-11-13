import unittest
import io
from pathlib import Path
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from app.database.connection import Database
from app.services.auto_comment_service import (
    AutoCommentService, AutoCommentValidationError, CommentItem, parse_comment_csv, select_comments,
)
from app.services.facebook_publishing_service import FacebookPublishingService
from app.services.project_service import ProjectService
from app.services.video_service import VideoService
from app.ui.pages.auto_comment_page import AutoCommentPage


class _ManualFacebookWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()
    instances = []

    def __init__(self, operation, parent=None):
        super().__init__(parent); self.operation = operation; self.started = False; self.interrupted = False; type(self).instances.append(self)

    def start(self): self.started = True
    def requestInterruption(self): self.interrupted = True


class AutoCommentPhase1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.sqlite3")
        self.project_id = ProjectService(self.database).create("Project", "p", str(Path(self.temp.name) / "videos"))
        VideoService(self.database).import_csv(self.project_id, "stt,title,url\n1,Title,https://www.facebook.com/watch/?v=1")
        self.video_id = self.database.list_videos(self.project_id)[0]["id"]
        FacebookPublishingService(self.database).save_pages(self.project_id, [{"id": "page-1", "name": "Page 1"}])

    def tearDown(self):
        self.database.close(); self.temp.cleanup()

    def _grant_page_read(self) -> None:
        with self.database.connection:
            self.database.connection.execute("UPDATE facebook_pages SET read_access_status='GRANTED' WHERE project_id=? AND page_id='page-1'", (self.project_id,))

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

    def test_page_post_read_requires_saved_token_and_read_permission(self):
        service = AutoCommentService(self.database)
        with patch("app.services.auto_comment_service.get_page_token", return_value="page-token"):
            with self.assertRaisesRegex(AutoCommentValidationError, "chưa được xác minh"):
                service.fetch_posts(self.project_id, "page-1", "2026-01-01", "2026-01-02", "all")
        FacebookPublishingService(self.database).save_pages(self.project_id, [
            {"id": "page-1", "name": "Page 1", "permissions": ["pages_manage_posts"]},
        ])
        with patch("app.services.auto_comment_service.get_page_token", return_value="page-token"):
            with self.assertRaisesRegex(AutoCommentValidationError, "chưa được xác minh"):
                service.fetch_posts(self.project_id, "page-1", "2026-01-01", "2026-01-02", "all")
        FacebookPublishingService(self.database).save_pages(self.project_id, [
            {"id": "page-1", "name": "Page 1", "permissions": ["pages_read_engagement"]},
        ])
        self._grant_page_read()
        with patch("app.services.auto_comment_service.get_page_token", return_value="page-token"):
            posts = service.fetch_posts(
                self.project_id, "page-1", "2026-01-01", "2026-01-02", "reels",
                request=lambda *_args: [
                    {"id": "post", "created_time": "2026-01-01T00:00:00+00:00", "post_type": "Post"},
                    {"id": "reel", "created_time": "2026-01-02T00:00:00+00:00", "post_type": "Reel"},
                ],
            )
        self.assertEqual(posts[0]["post_id"], "reel:reel")
        self.assertEqual(posts[0]["reel_id"], "reel")
        self.assertIsNone(posts[0]["source_post_id"])

    def test_live_sync_paginates_filters_deduplicates_and_keeps_source_ids_distinct(self):
        FacebookPublishingService(self.database).save_pages(self.project_id, [
            {"id": "page-1", "name": "Page 1", "permissions": ["pages_read_engagement"]},
        ])
        self._grant_page_read()
        calls = []
        def graph_get(path, params, _token):
            calls.append((path, dict(params)))
            if path == "page-1": return {"id": "page-1"}
            if path == "page-1/posts" and "after" not in params:
                return {"data": [{"id": "post-1", "created_time": "2026-01-02T12:00:00+00:00"}], "paging": {"cursors": {"after": "posts-2"}}}
            if path == "page-1/posts":
                return {"data": [{"id": "post-1", "created_time": "2026-01-02T12:00:00+00:00"}, {"id": "old", "created_time": "2025-12-31T12:00:00+00:00"}]}
            if path == "page-1/video_reels" and "after" not in params:
                return {"data": [{"id": "reel-1", "video_id": "video-1", "created_time": "2026-01-03T12:00:00+00:00"}], "paging": {"cursors": {"after": "reels-2"}}}
            return {"data": [{"id": "reel-2", "post_id": "post-for-reel", "video_id": "video-2", "created_time": "2026-01-04T12:00:00+00:00"}]}
        service = AutoCommentService(self.database, graph_get)
        with patch("app.services.auto_comment_service.get_page_token", return_value="page-token"):
            rows = service.fetch_posts(self.project_id, "page-1", "2026-01-01", "2026-01-04", "all")
        self.assertEqual({row["post_id"] for row in rows}, {"post-1", "reel:reel-1", "post-for-reel"})
        reel = next(row for row in rows if row["reel_id"] == "reel-2")
        self.assertEqual((reel["source_post_id"], reel["video_id"], reel["comment_target_id"]), ("post-for-reel", "video-2", "post-for-reel"))
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[1][1]["until"], "2026-01-05")

    def test_live_sync_rejects_page_token_for_another_page(self):
        FacebookPublishingService(self.database).save_pages(self.project_id, [
            {"id": "page-1", "name": "Page 1", "permissions": ["pages_read_engagement"]},
        ])
        self._grant_page_read()
        service = AutoCommentService(self.database, lambda *_args: {"id": "other-page"})
        with patch("app.services.auto_comment_service.get_page_token", return_value="page-token"):
            with self.assertRaisesRegex(AutoCommentValidationError, "không thuộc Fanpage"):
                service.fetch_posts(self.project_id, "page-1", "2026-01-01", "2026-01-02", "all")

    def test_live_sync_maps_expired_permission_and_network_errors_without_token_output(self):
        expired = HTTPError("https://graph.facebook.com", 401, "", {}, io.BytesIO(b'{"error":{"code":190}}'))
        with patch("app.services.auto_comment_service.urlopen", side_effect=expired):
            with self.assertRaisesRegex(AutoCommentValidationError, "hết hạn"):
                AutoCommentService._live_graph_get("page-1", {"fields": "id"}, "secret-token")
        denied = HTTPError("https://graph.facebook.com", 403, "", {}, io.BytesIO(b'{"error":{"code":200}}'))
        with patch("app.services.auto_comment_service.urlopen", side_effect=denied):
            with self.assertRaisesRegex(AutoCommentValidationError, "pages_read_engagement"):
                AutoCommentService._live_graph_get("page-1", {"fields": "id"}, "secret-token")
        with patch("app.services.auto_comment_service.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(AutoCommentValidationError, "kết nối"):
                AutoCommentService._live_graph_get("page-1", {"fields": "id"}, "secret-token")

    def test_saving_existing_campaign_updates_instead_of_duplicates(self):
        service = AutoCommentService(self.database)
        values = {"name": "Draft", "page_id": "page-1", "start_date": "2026-01-01", "end_date": "2026-01-02",
                  "content_type": "all", "selection_mode": "random", "count": 1, "prevent_duplicates": True}
        posts = [{"post_id": "post-1", "post_type": "Post", "published_at": "2026-01-01T00:00:00+00:00"}]
        campaign_id = service.save_preview(values, posts, {"post-1": ["Bình luận cũ"]})
        values["name"] = "Draft đã sửa"
        self.assertEqual(campaign_id, service.save_preview(values, posts, {"post-1": ["Bình luận mới"]}, campaign_id))
        self.assertEqual(self.database.connection.execute("SELECT COUNT(*) FROM auto_comment_campaigns").fetchone()[0], 1)
        campaign, rows = service.load_campaign_preview(campaign_id)
        self.assertEqual(campaign["name"], "Draft đã sửa")
        self.assertEqual([row["comment_text"] for row in rows], ["Bình luận mới"])

    def test_rerunning_migration_preserves_auto_comment_data(self):
        service = AutoCommentService(self.database)
        library_id = service.save_library("Thư viện cũ", [CommentItem("Xin chào Việt Nam")])
        from app.database.migrations import migrate
        migrate(self.database.connection)
        self.assertEqual(service.load_library(library_id)[0].comment_text, "Xin chào Việt Nam")

    def test_tab_switch_preserves_post_selection_and_campaign_save_respects_preview_selection(self):
        page = AutoCommentPage(self.database)
        page.page.setCurrentIndex(1)
        page.posts = [
            {"post_id": "post-1", "post_type": "Post", "published_at": "2026-01-01T00:00:00+00:00"},
            {"post_id": "post-2", "post_type": "Post", "published_at": "2026-01-02T00:00:00+00:00"},
        ]
        page.selected_post_ids = {"post-1"}; page._render_posts()
        page.tabs.setCurrentIndex(1); page.tabs.setCurrentIndex(0)
        self.assertEqual(page.selected_post_ids, {"post-1"})
        page.preview_comments = {"post-1": ["Một"], "post-2": ["Hai"]}; page._render_preview(page.posts)
        page.preview.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
        page.save_campaign()
        campaign = AutoCommentService(self.database).list_campaigns()[0]
        _, preview = AutoCommentService(self.database).load_campaign_preview(campaign["id"])
        self.assertEqual([row["post_id"] for row in preview], ["post-1"])

    def test_post_loading_uses_spinner_prevents_duplicates_and_reports_type_counts(self):
        _ManualFacebookWorker.instances.clear(); page = AutoCommentPage(self.database); page.page.setCurrentIndex(1)
        with patch("app.ui.pages.auto_comment_page.FacebookRequestWorker", _ManualFacebookWorker):
            page.load_posts(); page.load_posts()
            worker = _ManualFacebookWorker.instances[0]
            self.assertTrue(page.load_posts_button.is_loading); self.assertTrue(worker.started); self.assertEqual(len(_ManualFacebookWorker.instances), 1)
            worker.succeeded.emit([
                {"post_id": "post", "post_type": "Post", "published_at": "2026-01-01T00:00:00+00:00", "comment_target_id": "post"},
                {"post_id": "reel:1", "reel_id": "1", "post_type": "Reel", "published_at": "2026-01-01T00:00:00+00:00"},
            ]); worker.finished.emit()
        self.assertFalse(page.load_posts_button.is_loading)
        self.assertIn("1 bài viết và 1 Reels", page.post_status.text())

    def test_post_loading_error_keeps_existing_rows_and_close_stops_spinner(self):
        _ManualFacebookWorker.instances.clear(); page = AutoCommentPage(self.database); page.page.setCurrentIndex(1)
        page.posts=[{"post_id":"old","post_type":"Post","published_at":"2026-01-01T00:00:00+00:00","comment_target_id":"old"}];page.selected_post_ids={"old"};page._render_posts()
        with patch("app.ui.pages.auto_comment_page.FacebookRequestWorker", _ManualFacebookWorker):
            page.load_posts(); worker=_ManualFacebookWorker.instances[0]; worker.failed.emit("Lỗi mạng"); worker.finished.emit()
            self.assertEqual(page.posts_table.rowCount(), 1); self.assertIn("Lỗi mạng", page.post_status.text())
            page.load_posts(); worker=_ManualFacebookWorker.instances[1]; page.close()
        self.assertTrue(worker.interrupted); self.assertFalse(page.load_posts_button.is_loading)

    def test_preview_busy_state_and_completion_feedback(self):
        page=AutoCommentPage(self.database);page.posts=[{"post_id":"post","post_type":"Post","published_at":"2026-01-01T00:00:00+00:00"}];page.selected_post_ids={"post"};page.set_items([CommentItem("Bình luận")])
        def choose(*_args):
            self.assertTrue(page.preview_button.is_loading); return ["Bình luận"]
        with patch("app.ui.pages.auto_comment_page.select_comments", side_effect=choose): page.generate_preview()
        self.assertFalse(page.preview_button.is_loading); self.assertIn("Đã tạo 1 comment cho 1 bài viết", page.preview_status.text())

    def test_library_save_feedback_reports_success_and_error(self):
        page=AutoCommentPage(self.database);page.set_items([CommentItem("Bình luận")]);page.save_library();self.assertIn("Đã lưu",page.csv_status.text())
        with patch.object(page.service,"save_library",side_effect=AutoCommentValidationError("Tên trống")), patch("app.ui.pages.auto_comment_page.QMessageBox.warning"):
            page.save_library()
        self.assertIn("Không thể lưu",page.csv_status.text())

    def test_guided_workflow_validates_navigation_and_preserves_unsaved_state(self):
        page=AutoCommentPage(self.database)
        self.assertTrue(page.tabs.tabBar().isHidden());self.assertIn("Bước 1",page.workflow_progress.text())
        page._next_step();self.assertEqual(page.tabs.currentIndex(),0);self.assertIn("Fanpage",page.workflow_message.text())
        page.page.setCurrentIndex(1);page.posts=[{"post_id":"post","post_type":"Post","published_at":"2026-01-01T00:00:00+00:00","comment_target_id":"post"}];page.selected_post_ids={"post"};page._render_posts()
        page._next_step();self.assertEqual(page.tabs.currentIndex(),1)
        page._next_step();self.assertEqual(page.tabs.currentIndex(),1);self.assertIn("ít nhất một comment",page.workflow_message.text())
        page.set_items([CommentItem("Bình luận chưa lưu")]);page._next_step();self.assertEqual(page.tabs.currentIndex(),2)
        self.assertIn("Đã chọn: 1",page.workflow_progress.text());self.assertIn("Comment bật: 1",page.workflow_progress.text())
        page._go_to_step(0);page._go_to_step(1);self.assertEqual(page.selected_post_ids,{"post"});self.assertEqual(page.items()[0].comment_text,"Bình luận chưa lưu")

    def test_post_loading_ui_elements_match_modern_specs(self):
        _ManualFacebookWorker.instances.clear()
        page = AutoCommentPage(self.database)
        page.page.setCurrentIndex(1)
        self.assertEqual(page.load_posts_button.text(), "Tải bài viết")
        self.assertTrue(page.loading_container.isHidden())
        self.assertTrue(page.table_overlay.isHidden())

        with patch("app.ui.pages.auto_comment_page.FacebookRequestWorker", _ManualFacebookWorker):
            page.load_posts()
            worker = _ManualFacebookWorker.instances[0]
            self.assertTrue(page.load_posts_button.is_loading)
            self.assertEqual(page.load_posts_button.text(), "⌛ Đang tải bài viết...")
            self.assertFalse(page.load_posts_button.isEnabled())
            self.assertFalse(page.loading_container.isHidden())
            self.assertFalse(page.table_overlay.isHidden())

            worker.succeeded.emit([])
            worker.finished.emit()

        self.assertFalse(page.load_posts_button.is_loading)
        self.assertEqual(page.load_posts_button.text(), "Tải bài viết")
        self.assertTrue(page.load_posts_button.isEnabled())
        self.assertTrue(page.loading_container.isHidden())
        self.assertTrue(page.table_overlay.isHidden())


if __name__ == "__main__":
    unittest.main()
