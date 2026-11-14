import unittest
import io
from pathlib import Path
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest

from app.database.connection import Database
from app.services.auto_comment_service import (
    AutoCommentPageAccessError, AutoCommentService, AutoCommentValidationError, CommentItem, parse_comment_csv, select_comments,
)
from app.services.auto_comment_comment_service import AutoCommentCommentService, AutoCommentTargetError
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
        reel_request = next(params for path, params in calls if path == "page-1/video_reels")
        self.assertIn("video_id", reel_request["fields"])
        self.assertIn("post_id", reel_request["fields"])
        self.assertTrue(all(row["owner_page_id"] == "page-1" for row in rows))

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

    def test_campaign_execution_snapshot_is_single_use(self):
        service = AutoCommentService(self.database)
        values = {"name": "One reel", "page_id": "page-1", "start_date": "2026-01-01", "end_date": "2026-01-02",
                  "content_type": "Reels", "selection_mode": "sequential", "count": 1, "prevent_duplicates": True}
        post = {"post_id": "reel:1", "post_type": "Reel", "published_at": "2026-01-01T00:00:00+00:00"}
        campaign_id = service.save_preview(values, [post], {"reel:1": ["Bình luận đã duyệt"]}, approved_post_ids={"reel:1"})
        assignment = service.campaign_execution(campaign_id)[0]
        self.assertEqual((assignment["comment_text"], assignment["execution_status"]), ("Bình luận đã duyệt", "PENDING"))
        self.assertTrue(service.begin_execution(campaign_id, assignment["id"], "operation-1"))
        self.assertFalse(service.begin_execution(campaign_id, assignment["id"], "operation-2"))
        service.finish_execution(assignment["id"], {"status": "SUCCESS", "comment_id": "comment-1"})
        self.assertEqual(service.campaign_execution(campaign_id)[0]["execution_status"], "PUBLISHED")
        with self.assertRaisesRegex(AutoCommentValidationError, "snapshot"):
            service.save_preview(values, [post], {"reel:1": ["Nội dung khác"]}, campaign_id)
        self.assertEqual(service.campaign_execution(campaign_id)[0]["remote_comment_id"], "comment-1")

    def test_campaign_queue_runs_approved_assignments_sequentially_without_retrying_uncertain(self):
        service = AutoCommentService(self.database)
        values = {"name": "Queue", "project_id": self.project_id, "page_id": "page-1", "start_date": "2026-11-01", "end_date": "2026-11-13",
                  "content_type": "Reels", "selection_mode": "random", "count": 1, "prevent_duplicates": True,
                  "targets_by_post": {f"reel:{index}": {"status": "READY", "target_id": f"target-{index}"} for index in range(1, 4)}}
        posts = [{"post_id": f"reel:{index}", "reel_id": str(index), "post_type": "Reel", "published_at": "2026-11-10T00:00:00+00:00"} for index in range(1, 4)]
        campaign_id = service.save_preview(values, posts, {post["post_id"]: [f"Comment {index}"] for index, post in enumerate(posts, start=1)}, approved_post_ids={post["post_id"] for post in posts})
        calls = []; progress = []
        def validate(row):
            return {"status": "TARGET_UNVERIFIED", "message": "Không hỗ trợ"} if row["post_id"] == "reel:2" else {"status": "READY", "target_id": row["approved_target_id"]}
        def publish(row, operation_id):
            calls.append((row["post_id"], operation_id, row["comment_text"]))
            return {"status": "UNCERTAIN", "message": "Mạng không chắc chắn"} if row["post_id"] == "reel:3" else {"status": "SUCCESS", "comment_id": "comment-1", "verification_status": "READ_BACK"}
        results = service.execute_campaign_queue(campaign_id, validate, publish, lambda *args: progress.append(args))
        self.assertEqual([call[0] for call in calls], ["reel:1", "reel:3"])
        self.assertEqual(len({call[1] for call in calls}), 2)
        self.assertEqual([result["status"] for result in results], ["SUCCESS", "FAILED", "UNCERTAIN"])
        self.assertEqual(len(progress), 3)
        restored = {row["post_id"]: row["execution_status"] for row in AutoCommentService(self.database).campaign_execution(campaign_id)}
        self.assertEqual(restored, {"reel:1": "PUBLISHED", "reel:2": "FAILED", "reel:3": "UNCERTAIN"})
        self.assertEqual(AutoCommentService(self.database).execute_campaign_queue(campaign_id, validate, publish), [])
        self.assertEqual([call[0] for call in calls], ["reel:1", "reel:3"])

    def test_campaign_queue_stops_after_meta_rate_limit(self):
        service = AutoCommentService(self.database)
        values = {"name": "Rate limit", "project_id": self.project_id, "page_id": "page-1", "start_date": "2026-11-01", "end_date": "2026-11-13",
                  "content_type": "Reels", "selection_mode": "sequential", "count": 1, "prevent_duplicates": True,
                  "targets_by_post": {"reel:1": {"status": "READY", "target_id": "target-1"}, "reel:2": {"status": "READY", "target_id": "target-2"}}}
        posts = [{"post_id": "reel:1", "post_type": "Reel", "published_at": "2026-11-10T00:00:00+00:00"}, {"post_id": "reel:2", "post_type": "Reel", "published_at": "2026-11-10T00:00:00+00:00"}]
        campaign_id = service.save_preview(values, posts, {"reel:1": ["Một"], "reel:2": ["Hai"]}, approved_post_ids={"reel:1", "reel:2"})
        calls = []
        results = service.execute_campaign_queue(campaign_id, lambda row: {"status": "READY", "target_id": row["approved_target_id"]},
            lambda row, _operation: calls.append(row["post_id"]) or {"status": "FAILED", "error_category": "RATE_LIMIT", "message": "Meta chặn"})
        self.assertEqual((calls, len(results)), (["reel:1"], 1))
        restored = {row["post_id"]: row["execution_status"] for row in service.campaign_execution(campaign_id)}
        self.assertEqual(restored, {"reel:1": "FAILED", "reel:2": "PENDING"})

    def test_campaign_safety_review_flags_duplicate_near_duplicate_and_url(self):
        rows = [{"id": "a", "comment_text": "Mua ngay https://example.com/a", "execution_status": "PENDING"},
                {"id": "b", "comment_text": "Mua ngay https://example.com/a", "execution_status": "PENDING"},
                {"id": "c", "comment_text": "Mua ngay hôm nay", "execution_status": "UNCERTAIN"}]
        warnings = AutoCommentService.campaign_safety_review(rows)
        self.assertIn("trùng", " ".join(warnings["b"]).casefold())
        self.assertIn("URL", " ".join(warnings["b"]))
        self.assertIn("không tự gửi lại", " ".join(warnings["c"]))

    def test_queue_reuses_target_validation_for_three_approved_comments(self):
        service = AutoCommentService(self.database)
        posts = [{"post_id": f"reel:{number}", "reel_id": str(number), "post_type": "Reel",
                  "published_at": "2026-11-10T00:00:00+00:00"} for number in range(11)]
        values = {"name": "11 reels", "project_id": self.project_id, "page_id": "page-1",
                  "start_date": "2026-11-01", "end_date": "2026-11-13", "content_type": "Reels",
                  "selection_mode": "random", "count": 3, "prevent_duplicates": True,
                  "targets_by_post": {post["post_id"]: {"status": "READY", "target_id": f"target-{index}"}
                                      for index, post in enumerate(posts)}}
        campaign_id = service.save_preview(values, posts,
            {post["post_id"]: ["A", "B", "C"] for post in posts}, approved_post_ids={post["post_id"] for post in posts})
        validations = []; publishes = []
        def validate(row):
            validations.append(row["source_id"])
            return {"status": "READY", "target_id": row["approved_target_id"]}
        service.execute_campaign_queue(campaign_id, validate,
            lambda row, _operation: publishes.append(row["id"]) or {"status": "SUCCESS", "comment_id": row["id"]})
        self.assertEqual((len(validations), len(publishes)), (11, 33))

    def test_verification_session_and_persisted_cache_count_graph_gets(self):
        self._grant_page_read(); calls = []
        def graph_get(path, _params, _token):
            calls.append(path)
            if path == "page-1": return {"id": "page-1"}
            if path.endswith("/comments"): return {"data": []}
            return {"id": path}
        with patch("app.services.auto_comment_comment_service.get_page_token", return_value="page-token"), patch("app.services.auto_comment_service.get_page_token", return_value="page-token"):
            resolver = AutoCommentCommentService(self.database, graph_get=graph_get)
            session = resolver.begin_verification_session(self.project_id, "page-1")
            sources = [{"post_id": f"reel:{number}", "video_id": f"target-{number}", "owner_page_id": "page-1", "post_type": "Reel"}
                       for number in range(11)]
            for source in sources:
                self.assertEqual(resolver.resolve_target(self.project_id, "page-1", source, session=session)["status"], "READY")
            fresh_call_count = len(calls)
            self.assertTrue(resolver.resolve_target(self.project_id, "page-1", sources[0], session=session).get("cached"))
        self.assertEqual((fresh_call_count, len(calls), calls.count("page-1")), (23, 23, 1))

    def test_execution_ui_enables_multi_assignment_queue(self):
        target_service = AutoCommentCommentService(self.database)
        for index in (1, 2):
            target_service._save_target(self.project_id, "page-1", {"source_id": f"reel:{index}", "target_id": f"target-{index}",
                "target_type": "REEL", "status": "READY", "evidence": {"owner_id": "page-1"}}, "2026-11-14T00:00:00+00:00")
        page = AutoCommentPage(self.database); page.page.setCurrentIndex(1)
        page.posts = [{"post_id": f"reel:{index}", "reel_id": str(index), "post_type": "Reel", "published_at": "2026-11-10T00:00:00+00:00"} for index in (1, 2)]
        page.selected_post_ids = {"reel:1", "reel:2"}; page._restore_target_states()
        page.preview_comments = {"reel:1": ["Một"], "reel:2": ["Hai"]}; page._render_preview(page.posts)
        for row in range(page.preview.rowCount()): page.preview.item(row, 0).setCheckState(Qt.CheckState.Checked)
        page.save_campaign(); page.tabs.setCurrentIndex(3)
        self.assertTrue(page.execution_button.isEnabled())
        self.assertIn("Chờ gửi: 2", page.test_target_info.text())
        page.close()

    def test_step_four_explains_unsaved_36_assignment_preview_then_updates_after_save(self):
        page = AutoCommentPage(self.database); page.page.setCurrentIndex(1)
        page.posts = [{"post_id": f"reel:{index}", "post_type": "Reel", "published_at": "2026-11-10T00:00:00+00:00"} for index in range(12)]
        page.selected_post_ids = {post["post_id"] for post in page.posts}
        page.preview_comments = {post["post_id"]: ["A", "B", "C"] for post in page.posts}
        page.target_states = {post["post_id"]: {"status": "READY", "target_id": f"target-{index}"} for index, post in enumerate(page.posts)}
        page.approved_preview_assignments = {(post["post_id"], order) for post in page.posts for order in range(1, 4)}
        page._render_preview(page.posts); page.tabs.setCurrentIndex(3)
        self.assertIn("36 assignment đã duyệt nhưng chưa lưu", page.test_target_info.text())
        self.assertFalse(page.execution_button.isEnabled())
        page.save_campaign(); page.tabs.setCurrentIndex(3)
        self.assertIn("Assignment đã duyệt: 36", page.test_target_info.text())
        self.assertIn("Chờ gửi: 36", page.test_target_info.text())
        self.assertEqual(page.execution_button.text(), "Thực hiện chiến dịch (36 comment)")
        page.close()

    def test_11_reel_preview_saves_one_canonical_ready_assignment_and_reopens_it(self):
        page_id = "1490753847447865"; source_id = "122108499879536255"; target_id = "4703374409946917"
        FacebookPublishingService(self.database).save_pages(self.project_id, [{"id": page_id, "name": "Đậu Đậu Review"}])
        service = AutoCommentCommentService(self.database)
        service._save_target(self.project_id, page_id, {"source_id": source_id, "target_id": target_id,
                             "target_type": "REEL", "status": "READY", "evidence": {"owner_id": page_id}}, "2026-11-14T00:00:00+00:00")
        page = AutoCommentPage(self.database); page.page.setCurrentIndex(next(index for index in range(page.page.count()) if page.page.itemData(index) and page.page.itemData(index)[1] == page_id))
        ids = [source_id, "reel:1465931375512608"] + [f"reel:{number}" for number in range(9)]
        page.posts = [{"post_id": item, "post_type": "Reel", "published_at": "2026-11-10T00:00:00+00:00"} for item in ids]
        page.selected_post_ids = set(ids); page._restore_target_states(); page._render_posts()
        page.set_items([CommentItem(f"Comment {number}") for number in range(4)]); page.generate_preview()
        self.assertEqual(page.preview.rowCount(), 11)
        self.assertTrue(page.one.isChecked() and page.random_mode.isChecked())
        page.three.setChecked(True); self.assertTrue(page.three.isChecked() and page.random_mode.isChecked()); page.generate_preview()
        self.assertEqual((len(page.posts), len(page.selected_post_ids), page.preview.rowCount()), (11, 11, 33))
        self.assertEqual(len(page.approved_preview_assignments), 0)
        source_row = next(row for row in range(page.preview.rowCount()) if page.preview.item(row, 1).text() == source_id)
        page.preview.item(source_row, 0).setCheckState(Qt.CheckState.Checked); page.save_campaign()
        campaign_id = page.campaign_id; execution = AutoCommentService(self.database).campaign_execution(campaign_id)
        self.assertEqual(len(execution), 1)
        self.assertEqual((execution[0]["project_id"], execution[0]["page_id"], execution[0]["source_id"], execution[0]["approved_target_id"]),
                         (self.project_id, page_id, source_id, target_id))
        self.assertEqual(execution[0]["preflight_status"], "READY")
        self.assertNotEqual(execution[0]["source_id"], "reel:1465931375512608")
        with self.database.connection:
            self.database.connection.execute("UPDATE auto_comment_targets SET target_id='wrong-target' WHERE project_id=? AND page_id=? AND source_id=?", (self.project_id, page_id, source_id))
        self.assertEqual(AutoCommentService(self.database).campaign_execution(campaign_id)[0]["preflight_status"], "TARGET_ID_MISMATCH")
        with self.database.connection:
            self.database.connection.execute("UPDATE auto_comment_targets SET target_id=? WHERE project_id=? AND page_id=? AND source_id=?", (target_id, self.project_id, page_id, source_id))
        reopened = AutoCommentPage(self.database); reopened.page.setCurrentIndex(next(index for index in range(reopened.page.count()) if reopened.page.itemData(index) and reopened.page.itemData(index)[1] == page_id)); reopened.campaign_picker.setCurrentIndex(reopened.campaign_picker.findData(campaign_id))
        reopened.tabs.setCurrentIndex(3)
        self.assertEqual(reopened.approved_preview_assignments, {(source_id, 1)})
        self.assertIn(source_id, reopened.test_target_info.text()); self.assertIn(target_id, reopened.test_target_info.text())
        self.assertTrue(reopened.execution_button.isEnabled())
        campaign_count = len(AutoCommentService(self.database).list_campaigns()); page.campaign_id = None; page.approved_preview_assignments.clear(); page.save_campaign()
        self.assertEqual(len(AutoCommentService(self.database).list_campaigns()), campaign_count)
        self.assertIn("checkbox", page.preview_status.text())
        page.close(); reopened.close()

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
        page.preview.item(0, 0).setCheckState(Qt.CheckState.Checked)
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
            page.load_posts(); worker = _ManualFacebookWorker.instances[0]
            self.assertTrue(page.load_posts_button.is_loading)
            self.assertEqual(page.load_posts_button.text(), "⌛ Đang tải bài viết...")
            self.assertFalse(page.load_posts_button.isEnabled())
            self.assertFalse(page.loading_container.isHidden())
            worker.succeeded.emit([]); worker.finished.emit()
        self.assertFalse(page.load_posts_button.is_loading)
        self.assertEqual(page.load_posts_button.text(), "Tải bài viết")
        self.assertTrue(page.loading_container.isHidden())

    def test_target_resolver_requires_page_owned_readable_target_and_persists_evidence(self):
        self._grant_page_read()
        calls = []
        def graph_get(path, _params, _token):
            calls.append(path)
            if path == "page-1": return {"id": "page-1"}
            if path == "post-1": return {"id": "post-1", "from": {"id": "page-1"}}
            if path == "post-1/comments": return {"data": []}
            raise AssertionError(path)
        source = {"post_id": "post-1", "source_post_id": "post-1", "video_id": "video-1", "post_type": "Post"}
        with patch("app.services.auto_comment_comment_service.get_page_token", return_value="page-token"), patch("app.services.auto_comment_service.get_page_token", return_value="page-token"):
            result = AutoCommentCommentService(self.database, graph_get=graph_get).resolve_target(self.project_id, "page-1", source)
        self.assertEqual((result["status"], result["target_id"]), ("READY", "post-1"))
        self.assertEqual(calls, ["page-1", "post-1", "post-1/comments"])
        row = self.database.connection.execute("SELECT status,target_id,evidence_json FROM auto_comment_targets").fetchone()
        self.assertEqual((row["status"], row["target_id"]), ("READY", "post-1"))
        self.assertNotIn("page-token", row["evidence_json"])

    def test_reel_without_post_id_is_never_assumed_to_be_a_comment_target(self):
        self._grant_page_read()
        with patch("app.services.auto_comment_comment_service.get_page_token", return_value="page-token"), patch("app.services.auto_comment_service.get_page_token", return_value="page-token"):
            result = AutoCommentCommentService(self.database, graph_get=lambda *_: {"id": "page-1"}).resolve_target(
                self.project_id, "page-1", {"post_id": "reel:7", "post_type": "Reel"})
        self.assertEqual(result["status"], "TARGET_UNVERIFIED")
        self.assertIsNone(result["target_id"])

    def test_reel_video_candidate_uses_page_edge_ownership_without_equating_ids(self):
        self._grant_page_read()
        def graph_get(path, _params, _token):
            if path == "page-1": return {"id": "page-1"}
            if path == "video-9": return {"id": "video-9"}
            if path == "video-9/comments": return {"data": []}
            raise AssertionError(path)
        source = {"post_id": "reel:7", "reel_id": "reel-7", "video_id": "video-9", "owner_page_id": "page-1", "post_type": "Reel"}
        with patch("app.services.auto_comment_comment_service.get_page_token", return_value="page-token"), patch("app.services.auto_comment_service.get_page_token", return_value="page-token"):
            result = AutoCommentCommentService(self.database, graph_get=graph_get).resolve_target(self.project_id, "page-1", source)
        self.assertEqual((result["status"], result["target_id"]), ("READY", "video-9"))

    def test_reel_resolver_continues_after_an_unsupported_candidate(self):
        self._grant_page_read()
        def graph_get(path, _params, _token):
            if path == "page-1": return {"id": "page-1"}
            if path == "bad-post": raise AutoCommentPageAccessError("Facebook từ chối yêu cầu đọc (Graph API code 100, HTTP 400).")
            if path == "video-9": return {"id": "video-9"}
            if path == "video-9/comments": return {"data": []}
            raise AssertionError(path)
        source = {"post_id": "reel:7", "source_post_id": "bad-post", "video_id": "video-9", "owner_page_id": "page-1", "post_type": "Reel"}
        with patch("app.services.auto_comment_comment_service.get_page_token", return_value="page-token"), patch("app.services.auto_comment_service.get_page_token", return_value="page-token"):
            result = AutoCommentCommentService(self.database, graph_get=graph_get).resolve_target(self.project_id, "page-1", source)
        self.assertEqual((result["status"], result["target_id"]), ("READY", "video-9"))

    def test_ready_reel_target_is_restored_for_step_three_after_reload(self):
        service = AutoCommentCommentService(self.database)
        service._save_target(self.project_id, "page-1", {
            "source_id": "reel:7", "target_id": "video-9", "target_type": "REEL", "status": "READY",
            "evidence": {"owner_id": "page-1", "checked": "comments"},
        }, "2026-11-14T00:00:00+00:00")
        page = AutoCommentPage(self.database); page.page.setCurrentIndex(1)
        page.posts = [{"post_id": "reel:7", "reel_id": "reel-7", "video_id": "video-9", "owner_page_id": "page-1",
                       "post_type": "Reel", "published_at": "2026-11-01T00:00:00+00:00"}]
        page.selected_post_ids = {"reel:7"}; page._restore_target_states(); page._render_posts()
        self.assertEqual(page.target_states["reel:7"]["target_id"], "video-9")
        self.assertEqual(page.posts_table.item(0, 6).text(), "Sẵn sàng")
        page.tabs.setCurrentIndex(2)
        self.assertIn("Target sẵn sàng: 1", page.summary.text())
        page.close()

    def test_single_publish_requires_confirmation_and_never_retries_same_operation(self):
        service = AutoCommentCommentService(self.database, graph_get=lambda path, *_: {"id": path}, graph_post=lambda *_: {"id": "comment-1"})
        with self.assertRaises(AutoCommentValidationError):
            service.publish_one(self.project_id, "page-1", "post-1", "post-1", "Xin chào", confirmed=False, operation_id="op-1")
        with patch("app.services.auto_comment_comment_service.get_page_token", return_value="page-token"):
            result = service.publish_one(self.project_id, "page-1", "post-1", "post-1", "Xin chào", confirmed=True, operation_id="op-1")
        self.assertEqual((result["status"], result["verification_status"]), ("SUCCESS", "READ_BACK"))
        with self.assertRaisesRegex(AutoCommentValidationError, "không tự gửi lại"):
            service.publish_one(self.project_id, "page-1", "post-1", "post-1", "Xin chào", confirmed=True, operation_id="op-1")
        row = self.database.connection.execute("SELECT status,remote_comment_id FROM auto_comment_submission_attempts WHERE operation_id='op-1'").fetchone()
        self.assertEqual((row["status"], row["remote_comment_id"]), ("SUCCESS", "comment-1"))

    def test_single_publish_records_uncertain_and_rate_limit_without_retry(self):
        for operation_id, category, expected in (("op-network", "NETWORK_ERROR", "UNCERTAIN"), ("op-rate", "RATE_LIMIT", "FAILED")):
            service = AutoCommentCommentService(self.database, graph_post=lambda *_args, c=category: (_ for _ in ()).throw(AutoCommentTargetError(c, c)))
            with patch("app.services.auto_comment_comment_service.get_page_token", return_value="page-token"):
                result = service.publish_one(self.project_id, "page-1", "post-1", "post-1", "Xin chào", confirmed=True, operation_id=operation_id)
            self.assertEqual((result["status"], result["error_category"]), (expected, category))

    def test_ui_publish_requires_one_ready_target_and_explicit_confirmation(self):
        _ManualFacebookWorker.instances.clear()
        page = AutoCommentPage(self.database); page.page.setCurrentIndex(1)
        page.posts = [{"post_id": "post-1", "post_type": "Post", "published_at": "2026-01-01T00:00:00+00:00"}]
        page.selected_post_ids = {"post-1"}; page.manual_comment.setText("Xin chào")
        page.confirm_publish_one()
        self.assertIn("đúng một Reel hoặc bài viết", page.preview_status.text())
        page.target_states = {"post-1": {"status": "READY", "target_id": "post-1"}}
        page.posts.append({"post_id": "post-2", "post_type": "Post", "published_at": "2026-01-01T00:00:00+00:00"}); page.selected_post_ids.add("post-2")
        page.confirm_publish_one()
        self.assertIn("đúng một Reel hoặc bài viết", page.preview_status.text())
        page.posts.pop(); page.selected_post_ids.remove("post-2")
        with patch("app.ui.pages.auto_comment_page.FacebookRequestWorker", _ManualFacebookWorker), patch("app.ui.pages.auto_comment_page.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as confirmation:
            page.confirm_publish_one()
            worker = _ManualFacebookWorker.instances[0]
            self.assertTrue(worker.started); self.assertTrue(page.publish_one_button.is_loading)
            self.assertIn("Reel/bài viết: post-1", confirmation.call_args.args[2]); self.assertIn("Target comment: post-1", confirmation.call_args.args[2])
            worker.succeeded.emit({"status": "SUCCESS", "comment_id": "comment-1", "verification_status": "READ_BACK"}); worker.finished.emit()
        self.assertFalse(page.publish_one_button.is_loading)
        self.assertIn("comment-1", page.preview_status.text())

    def test_single_comment_tab_displays_target_and_persisted_history_filters(self):
        service = AutoCommentCommentService(self.database, graph_get=lambda path, *_: {"id": path}, graph_post=lambda *_: {"id": "comment-1"})
        service._save_target(self.project_id, "page-1", {"source_id": "reel:7", "target_id": "video-9",
                             "target_type": "REEL", "status": "READY", "evidence": {"owner_id": "page-1"}}, "2026-11-14T00:00:00+00:00")
        with patch("app.services.auto_comment_comment_service.get_page_token", return_value="page-token"):
            service.publish_one(self.project_id, "page-1", "reel:7", "video-9", "Bình luận thử", confirmed=True,
                                operation_id="history-op")
        page = AutoCommentPage(self.database); page.page.setCurrentIndex(1)
        page.posts = [{"post_id": "reel:7", "reel_id": "reel-7", "post_type": "Reel", "published_at": "2026-11-01T00:00:00+00:00"}]
        page.selected_post_ids = {"reel:7"}; page._restore_target_states(); page.manual_comment.setText("Bình luận thử"); page._render_posts()
        page.tabs.setCurrentIndex(3)
        self.assertIn("Chưa có campaign đã lưu", page.test_target_info.text())
        self.assertFalse(page.publish_one_button.isEnabled())
        page.history_page.setCurrentIndex(page.history_page.findData((self.project_id, "page-1"))); page.history_type.setCurrentText("REEL"); page.refresh_history()
        self.assertEqual(page.history_table.rowCount(), 1)
        self.assertEqual(page.history_table.item(0, 6).text(), "comment-1")
        self.assertIn("PUBLISHED", page.history_table.item(0, 7).text())
        self.assertEqual(page.history_table.item(0, 9).text(), "Không có liên kết trực tiếp")
        page.history_table.selectRow(0); page.copy_history_comment_id(); self.assertEqual(QApplication.clipboard().text(), "comment-1")
        page.history_status.setCurrentText("FAILED"); page.refresh_history(); self.assertEqual(page.history_table.rowCount(), 0)
        page.close()

    def test_native_checkbox_mouse_selection_model_handles_seven_mixed_rows_and_keyboard(self):
        page = AutoCommentPage(self.database)
        page.show(); self.app.processEvents()
        page.posts = [
            {"post_id": f"post-{index}", "source_post_id": f"post-{index}", "post_type": "Post", "published_at": "2026-01-01T00:00:00+00:00", "comment_target_id": f"post-{index}"}
            if index % 2 else {"post_id": f"reel:{index}", "reel_id": str(index), "post_type": "Reel", "published_at": "2026-01-01T00:00:00+00:00"}
            for index in range(1, 8)
        ]
        page.selected_post_ids = set(); page._render_posts()
        self.assertFalse(page.posts_table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable)
        self.assertIsNone(page.posts_table.indexWidget(page.posts_table.model().index(0, 0)))
        for row in range(3):
            rect = page.posts_table.visualItemRect(page.posts_table.item(row, 0))
            QTest.mouseClick(page.posts_table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                             rect.topLeft() + QPoint(10, rect.height() // 2))
            self.app.processEvents()
        self.assertEqual(page.selected_post_ids, {post["post_id"] for post in page.posts[:3]})
        self.assertTrue(all(page.posts_table.item(row, 0).checkState() == Qt.CheckState.Checked for row in range(3)))
        page.select_all_posts.click()
        self.assertEqual(page.selected_post_ids, {post["post_id"] for post in page.posts})
        self.assertTrue(all(page.posts_table.item(row, 0).checkState() == Qt.CheckState.Checked for row in range(7)))
        page.clear_all_posts.click()
        self.assertEqual(page.selected_post_ids, set())
        self.assertTrue(all(page.posts_table.item(row, 0).checkState() == Qt.CheckState.Unchecked for row in range(7)))
        page.posts_table.setCurrentCell(3, 0); page.posts_table.setFocus(); QTest.keyClick(page.posts_table, Qt.Key.Key_Space)
        self.assertEqual(page.selected_post_ids, {"reel:4"})
        page.posts_table.item(3, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(page.selected_post_ids, set())
        self.assertIn("Target sẵn sàng: 0", page.workflow_progress.text())
        page.close()



if __name__ == "__main__":
    unittest.main()
