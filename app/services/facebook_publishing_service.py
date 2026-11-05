"""Project-scoped Facebook Page publishing with durable job state.

The Meta client is injected so tests can use mocked Graph API responses and the
desktop UI can run it in its existing worker architecture.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.database.connection import Database

PUBLISH_STATUSES = (
    "PENDING", "SCHEDULED", "PREPARING", "UPLOADING", "PROCESSING",
    "PUBLISHING", "PUBLISHED", "FAILED", "PAUSED", "CANCELLED",
)
TRANSIENT_ERRORS = (TimeoutError, ConnectionError)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class FacebookPublishingError(RuntimeError):
    pass


class MetaGraphPublishingClient:
    """Small official Graph API client for Page video publishing."""

    def __init__(self, page_access_token: str, *, api_version: str = "v26.0", timeout: float = 60.0) -> None:
        if not page_access_token.strip():
            raise ValueError("Meta Page Access Token is required")
        self.token = page_access_token.strip()
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self.timeout = timeout

    def upload_video(self, *, page_id: str, file_path: str, title: str, description: str,
                     progress_callback: Callable[[float], None] | None = None) -> dict[str, Any]:
        boundary = "----VideoDownloaderFacebookBoundary"
        path = Path(file_path)
        data = path.read_bytes()
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\n{title}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"description\"\r\n\r\n{description}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"source\"; filename=\"{path.name}\"\r\nContent-Type: video/mp4\r\n\r\n".encode() + data + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        request = Request(
            f"{self.base_url}/{page_id}/videos?" + urlencode({"access_token": self.token}),
            data=b"".join(parts), method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        if progress_callback:
            progress_callback(0.0)
        payload = self._request(request)
        if progress_callback:
            progress_callback(100.0)
        return {"upload_id": payload.get("id") or payload.get("video_id"), **payload}

    def wait_for_processing(self, page_id: str, upload_id: str) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/{upload_id}?" + urlencode({"fields": "status,permalink_url", "access_token": self.token}),
            headers={"Accept": "application/json"},
        )
        payload = self._request(request)
        status = payload.get("status")
        if isinstance(status, dict) and str(status.get("video_status", "")).lower() in {"error", "failed"}:
            raise FacebookPublishingError("Meta không xử lý được video.")
        return payload

    @staticmethod
    def _request(request: Request) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                details = json.loads(exc.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                details = {}
            message = details.get("error", {}).get("message", "Meta Graph API request failed")
            raise FacebookPublishingError(str(message)) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FacebookPublishingError("Meta Graph API network request failed.") from exc
        if "error" in payload:
            raise FacebookPublishingError(str(payload["error"].get("message", "Meta Graph API error")))
        return payload


class FacebookPublishingService:
    def __init__(self, database: Database, client: Any | None = None) -> None:
        self.database = database
        self.client = client

    def save_pages(self, project_id: str, pages: list[dict[str, Any]], default_page_id: str | None = None) -> None:
        now = _now()
        connection = self.database.connection
        with connection:
            for page in pages:
                page_id = str(page["id"])
                connection.execute(
                    """INSERT INTO facebook_pages
                       (project_id,page_id,page_name,page_url,permissions_json,is_default,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(project_id,page_id) DO UPDATE SET
                       page_name=excluded.page_name,page_url=excluded.page_url,
                       permissions_json=excluded.permissions_json,is_default=excluded.is_default,
                       updated_at=excluded.updated_at""",
                    (project_id, page_id, str(page.get("name", "")), str(page.get("url", page.get("link", ""))),
                     json.dumps(page.get("permissions", []), ensure_ascii=False),
                     int(page_id == default_page_id), now, now),
                )
            if default_page_id:
                connection.execute(
                    "UPDATE facebook_pages SET is_default=0 WHERE project_id=? AND page_id<>?",
                    (project_id, default_page_id),
                )

    def list_pages(self, project_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.database.connection.execute(
            "SELECT * FROM facebook_pages WHERE project_id=? ORDER BY page_name COLLATE NOCASE", (project_id,)
        )]

    def list_drafts(self, project_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.database.connection.execute(
            """SELECT d.*, v.sequence_number, v.video_code, v.title AS video_title,
                      v.local_video_path FROM facebook_drafts d
               JOIN videos v ON v.id=d.video_id WHERE d.project_id=?
               ORDER BY v.sequence_number""", (project_id,)
        )]

    def save_draft(self, project_id: str, video_id: str, *, page_id: str | None,
                   facebook_title: str, description: str, hashtags: str) -> None:
        video = self.database.connection.execute(
            "SELECT id FROM videos WHERE id=? AND project_id=?", (video_id, project_id)
        ).fetchone()
        if video is None:
            raise FacebookPublishingError("Video không thuộc dự án hiện tại.")
        if page_id and self.database.connection.execute(
            "SELECT 1 FROM facebook_pages WHERE project_id=? AND page_id=?", (project_id, page_id)
        ).fetchone() is None:
            raise FacebookPublishingError("Fanpage không thuộc dự án hiện tại.")
        with self.database.connection:
            self.database.connection.execute(
                """INSERT INTO facebook_drafts
                   (project_id,video_id,page_id,facebook_title,description,hashtags,updated_at)
                   VALUES (?,?,?,?,?,?,?) ON CONFLICT(project_id,video_id) DO UPDATE SET
                   page_id=excluded.page_id,facebook_title=excluded.facebook_title,
                   description=excluded.description,hashtags=excluded.hashtags,updated_at=excluded.updated_at""",
                (project_id, video_id, page_id, facebook_title, description, hashtags, _now()),
            )

    def prepare_video(self, project_id: str, video_id: str) -> dict[str, Any]:
        row = self.database.connection.execute(
            "SELECT local_video_path FROM videos WHERE id=? AND project_id=?", (video_id, project_id)
        ).fetchone()
        if row is None:
            raise FacebookPublishingError("Video không thuộc dự án hiện tại.")
        path = Path(row["local_video_path"] or "")
        status, payload, error = "READY", {}, None
        if not path.is_file():
            status, error = "MISSING_FILE", "Không tìm thấy file video"
        else:
            try:
                payload = self._inspect_video(path)
                stream = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), {})
                payload = {"duration": payload.get("format", {}).get("duration"), "width": stream.get("width"), "height": stream.get("height"), "codec": stream.get("codec_name")}
                if not payload["width"] or not payload["height"]:
                    status, error = "UNSUPPORTED", "Chưa xác minh tương thích"
            except FacebookPublishingError as exc:
                status, error = "CHECK_ERROR", str(exc)
        with self.database.connection:
            self.database.connection.execute(
                """INSERT INTO facebook_drafts(project_id,video_id,facebook_title,preparation_status,preparation_json,last_error,updated_at)
                   SELECT ?,?,title,?,?,?,? FROM videos WHERE id=?
                   ON CONFLICT(project_id,video_id) DO UPDATE SET preparation_status=excluded.preparation_status,
                   preparation_json=excluded.preparation_json,last_error=excluded.last_error,updated_at=excluded.updated_at""",
                (project_id, video_id, status, json.dumps(payload, ensure_ascii=False), error, _now(), video_id),
            )
        return {"status": status, "metadata": payload, "error": error}

    def enqueue(self, project_id: str, video_id: str, page_id: str, *, title: str | None = None,
                description: str = "", scheduled_at: str | None = None, timezone: str = "UTC") -> str:
        video = self.database.connection.execute(
            "SELECT title, local_video_path, download_status FROM videos WHERE id=? AND project_id=?",
            (video_id, project_id),
        ).fetchone()
        if video is None:
            raise FacebookPublishingError("Video không thuộc dự án hiện tại.")
        if not video["local_video_path"] or not Path(video["local_video_path"]).is_file():
            raise FacebookPublishingError("Tệp video cục bộ không tồn tại.")
        if self.database.connection.execute(
            "SELECT 1 FROM facebook_pages WHERE project_id=? AND page_id=?", (project_id, page_id)
        ).fetchone() is None:
            raise FacebookPublishingError("Fanpage chưa được kết nối với dự án.")
        status = "SCHEDULED" if scheduled_at else "PENDING"
        job_id = str(uuid.uuid4())
        now = _now()
        try:
            with self.database.connection:
                self.database.connection.execute(
                    """INSERT INTO facebook_publishing_jobs
                       (id,project_id,video_id,page_id,local_file_path,title,description,scheduled_at,timezone,status,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, project_id, video_id, page_id, video["local_video_path"],
                     title if title is not None else video["title"], description, scheduled_at, timezone, status, now, now),
                )
        except Exception as exc:
            raise FacebookPublishingError("Video đã có job xuất bản đang hoạt động trên Fanpage này.") from exc
        return job_id

    def list_jobs(self, project_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.database.connection.execute(
            "SELECT * FROM facebook_publishing_jobs WHERE project_id=? ORDER BY created_at", (project_id,)
        )]

    def pause(self, job_id: str) -> None:
        self._set_status(job_id, "PAUSED")

    def resume(self, job_id: str) -> None:
        self._set_status(job_id, "SCHEDULED" if self.database.connection.execute(
            "SELECT scheduled_at FROM facebook_publishing_jobs WHERE id=?", (job_id,)
        ).fetchone()[0] else "PENDING")

    def retry(self, job_id: str) -> None:
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE facebook_publishing_jobs SET status='PENDING',progress=0,
                   last_error=NULL,retry_count=retry_count+1,updated_at=?
                   WHERE id=? AND status='FAILED'""", (_now(), job_id)
            )

    def due_jobs(self, project_id: str, now: datetime | None = None) -> list[dict[str, Any]]:
        moment = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        return [dict(row) for row in self.database.connection.execute(
            """SELECT * FROM facebook_publishing_jobs
               WHERE project_id=? AND status='SCHEDULED' AND scheduled_at<=?
               ORDER BY scheduled_at, created_at""", (project_id, moment)
        )]

    def _set_status(self, job_id: str, status: str) -> None:
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE facebook_publishing_jobs SET status=?,updated_at=? WHERE id=?", (status, _now(), job_id)
            )

    def update_progress(self, job_id: str, progress: float, status: str | None = None) -> None:
        if status is not None and status not in PUBLISH_STATUSES:
            raise ValueError("Trạng thái xuất bản không hợp lệ.")
        fields = "progress=?, updated_at=?"
        values: list[Any] = [max(0.0, min(100.0, float(progress))), _now()]
        if status:
            fields = "status=?, " + fields
            values.insert(0, status)
        values.append(job_id)
        with self.database.connection:
            self.database.connection.execute(f"UPDATE facebook_publishing_jobs SET {fields} WHERE id=?", values)

    def publish(self, job_id: str, *, progress: Callable[[float], None] | None = None) -> dict[str, Any]:
        if self.client is None:
            raise FacebookPublishingError("Chưa cấu hình Meta Graph API client.")
        job = self.database.connection.execute(
            "SELECT * FROM facebook_publishing_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if job is None:
            raise FacebookPublishingError("Không tìm thấy job xuất bản.")
        path = Path(job["local_file_path"])
        if not path.is_file():
            self._fail(job_id, "Tệp video cục bộ không tồn tại.")
            raise FacebookPublishingError("Tệp video cục bộ không tồn tại.")
        self.update_progress(job_id, 0, "PREPARING")
        try:
            self._inspect_video(path)
        except FacebookPublishingError as exc:
            self._fail(job_id, str(exc))
            raise
        self.update_progress(job_id, 0, "UPLOADING")
        try:
            result = self.client.upload_video(
                page_id=job["page_id"], file_path=str(path), title=job["title"],
                description=job["description"], progress_callback=lambda value: self._progress(job_id, value, progress),
            )
        except Exception as exc:
            self._fail(job_id, str(exc))
            raise FacebookPublishingError(str(exc)) from exc
        upload_id = str(result.get("upload_id") or result.get("id") or "")
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE facebook_publishing_jobs SET remote_upload_id=?, status='PROCESSING', updated_at=? WHERE id=?",
                (upload_id, _now(), job_id),
            )
        try:
            remote = self.client.wait_for_processing(job["page_id"], upload_id)
        except Exception as exc:
            self._fail(job_id, str(exc))
            raise FacebookPublishingError(str(exc)) from exc
        post_id = str(remote.get("post_id") or remote.get("id") or upload_id)
        url = remote.get("url") or remote.get("permalink_url")
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE facebook_publishing_jobs SET status='PUBLISHED',progress=100,
                   published_post_id=?,published_url=?,updated_at=? WHERE id=?""",
                (post_id, url, _now(), job_id),
            )
        if progress:
            progress(100.0)
        return dict(self.database.connection.execute("SELECT * FROM facebook_publishing_jobs WHERE id=?", (job_id,)).fetchone())

    def _progress(self, job_id: str, value: float, callback: Callable[[float], None] | None) -> None:
        self.update_progress(job_id, value, "UPLOADING")
        if callback:
            callback(value)

    def _fail(self, job_id: str, message: str) -> None:
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE facebook_publishing_jobs SET status='FAILED',last_error=?,updated_at=? WHERE id=?",
                (message, _now(), job_id),
            )

    @staticmethod
    def _inspect_video(path: Path) -> dict[str, Any]:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                capture_output=True, text=True, check=True, timeout=30,
            )
            payload = json.loads(result.stdout or "{}")
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            raise FacebookPublishingError("Không thể kiểm tra metadata video bằng ffprobe.") from exc
        if not any(stream.get("codec_type") == "video" for stream in payload.get("streams", [])):
            raise FacebookPublishingError("Tệp không chứa video được hỗ trợ.")
        return payload

