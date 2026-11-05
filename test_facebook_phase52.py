import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.connection import Database
from app.services.facebook_publishing_service import (
    FacebookPublishingPending,
    FacebookPublishingService,
)
from app.services.project_service import ProjectService
from app.services.video_service import VideoService


class _PublishingClient:
    def __init__(self, result):
        self.result = result
        self.uploaded = []

    def upload_video(self, **kwargs):
        self.uploaded.append(kwargs)
        kwargs["progress_callback"](25)
        kwargs["progress_callback"](100)
        return {"upload_id": "remote-1"}

    def wait_for_processing(self, page_id, upload_id):
        return self.result


class FacebookPhase52Tests(unittest.TestCase):
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
        self.video_path.write_bytes(b"video")
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE videos SET local_video_path=? WHERE id=?", (str(self.video_path), self.video_id)
            )
        self.service = FacebookPublishingService(self.database)
        self.inspect_patcher = patch.object(
            self.service, "_inspect_video", return_value={"streams": [{"codec_type": "video"}]}
        )
        self.inspect_patcher.start()
        self.service.save_pages(self.project_id, [{"id": "page-1", "name": "Page 1"}], "page-1")
        self.service.save_draft(
            self.project_id, self.video_id, page_id="page-1", facebook_title="FB title",
            description="Caption", hashtags="#tag",
        )
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE facebook_drafts SET preparation_status='READY' WHERE project_id=? AND video_id=?",
                (self.project_id, self.video_id),
            )

    def tearDown(self):
        self.inspect_patcher.stop()
        self.database.close()
        self.temp.cleanup()

    def test_success_persists_remote_id_url_and_progress(self):
        job_id = self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")
        client = _PublishingClient({"id": "post-1", "permalink_url": "https://facebook.com/post-1"})
        self.service.client = client
        progress = []
        with patch.object(self.service, "_inspect_video", return_value={"streams": [{"codec_type": "video"}]}):
            result = self.service.publish(job_id, progress=progress.append)
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["published_post_id"], "post-1")
        self.assertEqual(result["published_url"], "https://facebook.com/post-1")
        self.assertEqual(progress, [25, 100, 100])

    def test_processing_pending_is_not_marked_published(self):
        job_id = self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")
        self.service.client = _PublishingClient({"status": {"video_status": "processing"}, "id": "remote-1"})
        with patch.object(self.service, "_inspect_video", return_value={"streams": [{"codec_type": "video"}]}):
            with self.assertRaises(FacebookPublishingPending):
                self.service.publish(job_id)
        status = self.service.list_jobs(self.project_id)[0]["status"]
        self.assertEqual(status, "PUBLISHING")

    def test_processing_error_preserves_meta_status_details(self):
        job_id = self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")
        self.service.client = _PublishingClient({
            "id": "remote-1",
            "status": {
                "video_status": "error",
                "uploading_phase": {"status": "complete"},
                "processing_phase": {"status": "complete"},
                "publishing_phase": {"status": "not_started"},
            },
        })
        with patch.object(self.service, "_inspect_video", return_value={"streams": [{"codec_type": "video"}]}):
            with self.assertRaisesRegex(Exception, "video_status.*error"):
                self.service.publish(job_id)
        job = self.service.list_jobs(self.project_id)[0]
        self.assertEqual(job["status"], "FAILED")
        self.assertIn("publishing_phase", job["last_error"])

    def test_processing_requires_published_phase_and_normalizes_permalink(self):
        job_id = self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")
        self.service.client = _PublishingClient({
            "id": "remote-1",
            "status": {"video_status": "ready", "publishing_phase": {"status": "published"}},
            "permalink_url": "/reel/remote-1/",
        })
        with patch.object(self.service, "_inspect_video", return_value={"streams": [{"codec_type": "video"}]}):
            result = self.service.publish(job_id)
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["published_url"], "https://www.facebook.com/reel/remote-1/")

    def test_active_duplicate_is_rejected_before_second_upload(self):
        self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")
        with self.assertRaises(Exception):
            self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")

    def test_selected_file_is_persisted_and_uploaded_without_fallback(self):
        selected = Path(self.temp.name) / "h264-test.mp4"
        selected.write_bytes(b"selected-media")
        identity = self.service.file_identity(selected)
        job_id = self.service.enqueue_from_draft(
            self.project_id, self.video_id, "page-1",
            file_path=selected, confirmed_identity=identity,
        )
        job = self.service.list_jobs(self.project_id)[0]
        self.assertEqual(job["local_file_path"], str(selected))
        self.assertEqual(job["local_file_identity"], json.dumps(identity, ensure_ascii=False, sort_keys=True))
        self.service.client = _PublishingClient({"id": "post-selected", "permalink_url": "https://facebook.com/selected"})
        result = self.service.publish(job_id)
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(self.service.client.uploaded[0]["file_path"], str(selected))

    def test_changed_selected_file_stops_before_upload(self):
        selected = Path(self.temp.name) / "h264-test.mp4"
        selected.write_bytes(b"selected-media")
        job_id = self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1", file_path=selected)
        selected.write_bytes(b"changed-media")
        client = _PublishingClient({"id": "post-selected", "permalink_url": "https://facebook.com/selected"})
        self.service.client = client
        with self.assertRaisesRegex(Exception, "thay đổi"):
            self.service.publish(job_id)
        self.assertEqual(client.uploaded, [])
        self.assertEqual(self.service.list_jobs(self.project_id)[0]["status"], "FAILED")

    def test_reconcile_meta_failure_preserves_remote_identifiers(self):
        job_id = self.service.enqueue_from_draft(self.project_id, self.video_id, "page-1")
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE facebook_publishing_jobs SET status='PUBLISHING', remote_video_id=?, upload_session_id=? WHERE id=?",
                ("remote-av1", "session-av1", job_id),
            )
        result = self.service.reconcile_meta_failure(
            job_id,
            {"id": "remote-av1", "status": {"video_status": "error", "processing_phase": {"status": "complete"}}},
            {"http_status": 200, "endpoint": "/v26.0/remote-av1", "result": "PASS"},
        )
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["remote_video_id"], "remote-av1")
        self.assertEqual(result["upload_session_id"], "session-av1")
        self.assertIn("META_PROCESSING_FAILED", result["last_error"])


if __name__ == "__main__":
    unittest.main()
