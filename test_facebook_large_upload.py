import tempfile
import unittest
import io
import json
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from app.services.facebook_publishing_service import (
    FacebookPublishingError,
    MetaGraphPublishingClient,
)


class _ResumableClient(MetaGraphPublishingClient):
    def __init__(self):
        super().__init__("test-token")
        self.offset = 0
        self.chunk_sizes = []
        self.calls = []

    def _post_json(self, page_id, params):
        self.calls.append(("json", params.copy()))
        if params["upload_phase"] == "start":
            return {"upload_session_id": "session-1", "video_id": "remote-1", "start_offset": "0", "end_offset": "0"}
        return {"success": True, "video_id": "remote-1"}

    def _post_multipart(self, page_id, params, field, data, **kwargs):
        self.calls.append(("transfer", params.copy()))
        self.chunk_sizes.append(len(data))
        self.offset += len(data)
        return {"start_offset": str(params["start_offset"]), "end_offset": str(self.offset)}


class _BadOffsetClient(_ResumableClient):
    def _post_multipart(self, page_id, params, field, data, **kwargs):
        return {"start_offset": "0", "end_offset": "0"}


class _RequestCaptureClient(MetaGraphPublishingClient):
    def __init__(self):
        super().__init__("secret-token")
        self.requests = []

    def _request(self, request):
        self.requests.append(request)
        return {"ok": True}


class FacebookLargeUploadTests(unittest.TestCase):
    def test_streams_chunks_and_reports_real_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.mp4"
            path.write_bytes(b"x" * (8 * 1024 * 1024 + 123))
            client = _ResumableClient()
            progress = []
            checkpoints = []
            result = client.upload_video(
                page_id="page-1", file_path=str(path), title="Title", description="Caption",
                progress_callback=progress.append, checkpoint_callback=checkpoints.append,
            )
            self.assertEqual(result["upload_session_id"], "session-1")
            self.assertEqual(result["remote_video_id"], "remote-1")
            self.assertEqual(max(client.chunk_sizes), 8 * 1024 * 1024)
            self.assertEqual(progress[-1], 100.0)
            self.assertEqual(checkpoints[0]["upload_phase"], "START")
            self.assertEqual(checkpoints[-1]["upload_phase"], "FINISH")
            self.assertEqual(path.stat().st_size, 8 * 1024 * 1024 + 123)

    def test_invalid_meta_offset_stops_without_infinite_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"x" * 100)
            with self.assertRaises(FacebookPublishingError):
                _BadOffsetClient().upload_video(page_id="page-1", file_path=str(path), title="", description="")

    def test_network_diagnostic_contains_endpoint_and_exception_type_only(self):
        client = MetaGraphPublishingClient("secret-token")
        request = Request("https://graph.facebook.com/v26.0/page-1/videos", method="POST")
        with patch("app.services.facebook_publishing_service.urlopen", side_effect=URLError("reset")):
            with self.assertRaisesRegex(FacebookPublishingError, "URLError"):
                client._request(request)
        self.assertEqual(client.diagnostics[0]["endpoint"], "/v26.0/page-1/videos")
        self.assertNotIn("secret-token", str(client.diagnostics))

    def test_upload_uses_graph_video_and_file_part_metadata(self):
        client = _RequestCaptureClient()
        client._post_json("page-1", {"upload_phase": "start", "file_size": "3"})
        client._post_multipart("page-1", {"upload_phase": "transfer"}, "video_file_chunk", b"abc", filename="video.mp4", content_type="video/mp4")
        self.assertTrue(all("graph-video.facebook.com" in request.full_url for request in client.requests))
        multipart_request = client.requests[1]
        body = multipart_request.data
        self.assertIn(b'filename="video.mp4"', body)
        self.assertIn(b"Content-Type: video/mp4", body)

    def test_meta_error_code_and_subcode_are_preserved_without_credentials(self):
        client = MetaGraphPublishingClient("secret-token")
        request = Request("https://graph-video.facebook.com/v26.0/page-1/videos", method="POST")
        payload = json.dumps({"error": {"message": "Invalid parameter", "code": 100, "error_subcode": 1363030, "fbtrace_id": "trace-1"}}).encode()
        error = HTTPError(request.full_url, 400, "bad request", {}, io.BytesIO(payload))
        with patch("app.services.facebook_publishing_service.urlopen", side_effect=error):
            with self.assertRaisesRegex(FacebookPublishingError, "100/1363030"):
                client._request(request)
        self.assertEqual(client.diagnostics[0]["meta_code"], "100")
        self.assertEqual(client.diagnostics[0]["meta_subcode"], "1363030")
        self.assertNotIn("secret-token", str(client.diagnostics))


if __name__ == "__main__":
    unittest.main()
