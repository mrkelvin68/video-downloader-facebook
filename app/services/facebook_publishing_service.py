"""Project-scoped Facebook Page publishing with durable job state.

The Meta client is injected so tests can use mocked Graph API responses and the
desktop UI can run it in its existing worker architecture.
"""
from __future__ import annotations

import json
import hashlib
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
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


class FacebookPublishingPending(FacebookPublishingError):
    """Meta accepted the upload but has not confirmed publication yet."""


class MetaGraphPublishingClient:
    """Small official Graph API client for Page video publishing."""

    def __init__(self, page_access_token: str, *, api_version: str = "v26.0", timeout: float = 60.0) -> None:
        if not page_access_token.strip():
            raise ValueError("Meta Page Access Token is required")
        self.token = page_access_token.strip()
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self.upload_base_url = f"https://graph-video.facebook.com/{api_version}"
        self.timeout = timeout
        self.diagnostics: list[dict[str, Any]] = []

    def upload_video(self, *, page_id: str, file_path: str, title: str, description: str,
                     progress_callback: Callable[[float], None] | None = None,
                     checkpoint_callback: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        path = Path(file_path)
        file_size = path.stat().st_size
        start = self._post_json(page_id, {"upload_phase": "start", "file_size": str(file_size)})
        session_id = str(start.get("upload_session_id") or "")
        remote_video_id = str(start.get("video_id") or start.get("id") or "")
        if not session_id:
            raise FacebookPublishingError("Meta không trả về upload session cho video.")
        next_offset = int(start.get("start_offset") or 0)
        end_offset = int(start.get("end_offset") or next_offset)
        self._checkpoint(checkpoint_callback, session_id, remote_video_id, next_offset, end_offset, "START")
        chunk_size = 8 * 1024 * 1024
        iterations = 0
        with path.open("rb") as stream:
            while next_offset < file_size:
                iterations += 1
                if iterations > (file_size // chunk_size) + 100:
                    raise FacebookPublishingError("Meta trả về offset upload không hợp lệ.")
                stream.seek(next_offset)
                chunk = stream.read(min(chunk_size, file_size - next_offset))
                if not chunk:
                    raise FacebookPublishingError("Không đọc được chunk video cần upload.")
                transfer = self._post_multipart(
                    page_id,
                    {"upload_phase": "transfer", "upload_session_id": session_id, "start_offset": str(next_offset)},
                    "video_file_chunk", chunk, filename=path.name, content_type="video/mp4",
                )
                confirmed = int(transfer.get("end_offset") or 0)
                returned_start = int(transfer.get("start_offset") or next_offset)
                if confirmed <= next_offset or confirmed > file_size:
                    raise FacebookPublishingError("Meta trả về offset upload không hợp lệ.")
                next_offset = confirmed
                end_offset = int(transfer.get("end_offset") or confirmed)
                self._checkpoint(checkpoint_callback, session_id, remote_video_id, returned_start, end_offset, "TRANSFER")
                if progress_callback:
                    progress_callback(next_offset * 100.0 / file_size)
        finish_params = {
            "upload_phase": "finish", "upload_session_id": session_id,
            "title": title, "description": description, "published": "true",
        }
        if remote_video_id:
            finish_params["video_id"] = remote_video_id
        finish = self._post_json(page_id, finish_params)
        final_video_id = str(finish.get("video_id") or finish.get("id") or remote_video_id or "")
        if not final_video_id:
            raise FacebookPublishingPending("Meta đã hoàn tất upload nhưng chưa trả về video ID.")
        self._checkpoint(checkpoint_callback, session_id, final_video_id, file_size, file_size, "FINISH")
        return {"upload_id": final_video_id, "remote_video_id": final_video_id, "upload_session_id": session_id, **finish}

    def _checkpoint(self, callback, session_id: str, video_id: str, start: int, end: int, phase: str) -> None:
        if callback:
            callback({"upload_session_id": session_id, "remote_video_id": video_id, "start_offset": start, "end_offset": end, "upload_phase": phase})

    def _post_json(self, page_id: str, params: dict[str, str]) -> dict[str, Any]:
        query = {**params, "access_token": self.token}
        request = Request(f"{self.upload_base_url}/{page_id}/videos?{urlencode(query)}", data=b"", method="POST")
        return self._request(request)

    def _post_multipart(self, page_id: str, params: dict[str, str], field: str, data: bytes,
                        *, filename: str, content_type: str) -> dict[str, Any]:
        boundary = "----VideoDownloaderFacebookChunk"
        fields = b"".join(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
            for key, value in params.items()
        )
        file_header = f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode()
        body = fields + file_header + data + f"\r\n--{boundary}--\r\n".encode()
        query = {"access_token": self.token}
        request = Request(
            f"{self.upload_base_url}/{page_id}/videos?{urlencode(query)}", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return self._request(request)

    def wait_for_processing(self, page_id: str, upload_id: str) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/{upload_id}?" + urlencode({"fields": "status,permalink_url", "access_token": self.token}),
            headers={"Accept": "application/json"},
        )
        payload = self._request(request)
        status = payload.get("status")
        if isinstance(status, dict):
            processing = status.get("processing_phase")
            publishing = status.get("publishing_phase")
            processing = processing if isinstance(processing, dict) else {}
            publishing = publishing if isinstance(publishing, dict) else {}
            video_state = str(status.get("video_status") or processing.get("status") or "").casefold()
            publish_state = str(
                publishing.get("status") or publishing.get("publish_status") or ""
            ).casefold()
            if video_state in {"error", "failed"} or publish_state in {"error", "failed"}:
                detail = json.dumps(status, ensure_ascii=False, sort_keys=True)
                raise FacebookPublishingError(f"Meta processing error: {detail}")
            if video_state in {"processing", "uploading", "transcoding", "pending"}:
                raise FacebookPublishingPending("Meta đang xử lý video; chưa thể xác nhận đã đăng.")
            permalink = str(payload.get("permalink_url") or "").strip()
            ready_states = {"ready", "complete", "completed"}
            published_states = {"complete", "completed", "published"}
            if permalink and not permalink.startswith(("http://", "https://")):
                permalink = "https://www.facebook.com" + (
                    permalink if permalink.startswith("/") else "/" + permalink
                )
            if not (video_state in ready_states and publish_state in published_states and permalink):
                raise FacebookPublishingPending(
                    "Meta đã nhận video nhưng chưa xác nhận hoàn tất xử lý và xuất bản."
                )
            if permalink != payload.get("permalink_url"):
                payload = dict(payload)
                payload["permalink_url"] = permalink
        return payload

    def _request(self, request: Request) -> dict[str, Any]:
        endpoint = urlsplit(request.full_url).path
        method = getattr(request, "method", None) or ("POST" if request.data is not None else "GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.diagnostics.append({"method": method, "endpoint": endpoint, "http_status": response.status, "result": "PASS"})
        except HTTPError as exc:
            try:
                details = json.loads(exc.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                details = {}
            error = details.get("error", {})
            code = str(error.get("code") or "")
            subcode = str(error.get("error_subcode") or "")
            self.diagnostics.append({"method": method, "endpoint": endpoint, "http_status": exc.code, "meta_code": code or None, "meta_subcode": subcode or None, "fbtrace_id": error.get("fbtrace_id"), "result": "FAILED"})
            message = error.get("message", "Meta Graph API request failed")
            if code:
                message = f"Meta API error code {code}{f'/{subcode}' if subcode else ''}: {message}"
            raise FacebookPublishingError(str(message)) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.diagnostics.append({"method": method, "endpoint": endpoint, "error_type": type(exc).__name__, "result": "FAILED"})
            raise FacebookPublishingError(f"Meta Graph API network request failed ({type(exc).__name__}) at {endpoint}.") from exc
        if "error" in payload:
            error = payload["error"]
            code = str(error.get("code") or "")
            subcode = str(error.get("error_subcode") or "")
            self.diagnostics.append({"method": method, "endpoint": endpoint, "http_status": 200, "meta_code": code or None, "meta_subcode": subcode or None, "fbtrace_id": error.get("fbtrace_id"), "result": "FAILED"})
            message = str(error.get("message", "Meta Graph API error"))
            if code:
                message = f"Meta API error code {code}{f'/{subcode}' if subcode else ''}: {message}"
            raise FacebookPublishingError(message)
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

    def prepare_video(self, project_id: str, video_id: str, file_path: str | Path | None = None) -> dict[str, Any]:
        row = self.database.connection.execute(
            "SELECT local_video_path FROM videos WHERE id=? AND project_id=?", (video_id, project_id)
        ).fetchone()
        if row is None:
            raise FacebookPublishingError("Video không thuộc dự án hiện tại.")
        path = Path(file_path or row["local_video_path"] or "")
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
                description: str = "", scheduled_at: str | None = None, timezone: str = "UTC",
                file_path: str | Path | None = None,
                confirmed_identity: dict[str, Any] | None = None) -> str:
        video = self.database.connection.execute(
            "SELECT title, local_video_path, download_status FROM videos WHERE id=? AND project_id=?",
            (video_id, project_id),
        ).fetchone()
        if video is None:
            raise FacebookPublishingError("Video không thuộc dự án hiện tại.")
        selected_path = Path(file_path or video["local_video_path"] or "")
        if not selected_path.is_file():
            raise FacebookPublishingError("Tệp video cục bộ không tồn tại.")
        self._inspect_video(selected_path)
        identity = self.file_identity(selected_path)
        if confirmed_identity is not None and identity != confirmed_identity:
            raise FacebookPublishingError("File video đã thay đổi sau khi xác nhận; hãy xác nhận lại file.")
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
                       (id,project_id,video_id,page_id,local_file_path,title,description,scheduled_at,timezone,status,created_at,updated_at,local_file_identity)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, project_id, video_id, page_id, str(selected_path),
                     title if title is not None else video["title"], description, scheduled_at, timezone, status, now, now,
                     json.dumps(identity, ensure_ascii=False, sort_keys=True)),
                )
        except Exception as exc:
            raise FacebookPublishingError("Video đã có job xuất bản đang hoạt động trên Fanpage này.") from exc
        return job_id

    def enqueue_from_draft(self, project_id: str, video_id: str, page_id: str, *,
                           file_path: str | Path | None = None,
                           confirmed_identity: dict[str, Any] | None = None) -> str:
        draft = self.database.connection.execute(
            "SELECT * FROM facebook_drafts WHERE project_id=? AND video_id=?",
            (project_id, video_id),
        ).fetchone()
        if draft is None:
            raise FacebookPublishingError("Hãy lưu nội dung đăng trước khi xuất bản.")
        if str(draft["preparation_status"] or "") != "READY":
            raise FacebookPublishingError("Video chưa vượt qua bước kiểm tra media.")
        description = str(draft["description"] or "")
        hashtags = str(draft["hashtags"] or "").strip()
        if hashtags:
            description = f"{description}\n\n{hashtags}" if description else hashtags
        return self.enqueue(
            project_id, video_id, page_id, title=draft["facebook_title"],
            description=description,
            file_path=file_path,
            confirmed_identity=confirmed_identity,
        )

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

    def reconcile_meta_failure(self, job_id: str, remote_status: dict[str, Any],
                               diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist a confirmed terminal Meta failure without creating a new upload."""
        job = self.database.connection.execute(
            "SELECT id,remote_video_id,page_id,status FROM facebook_publishing_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if job is None:
            raise FacebookPublishingError("Không tìm thấy job xuất bản.")
        remote_id = str(remote_status.get("id") or "")
        if remote_id and job["remote_video_id"] and remote_id != str(job["remote_video_id"]):
            raise FacebookPublishingError("Remote Video ID không khớp job cần reconcile.")
        status = remote_status.get("status") if isinstance(remote_status.get("status"), dict) else {}
        video_state = str(status.get("video_status") or "").casefold()
        processing = status.get("processing_phase") if isinstance(status.get("processing_phase"), dict) else {}
        publishing = status.get("publishing_phase") if isinstance(status.get("publishing_phase"), dict) else {}
        if video_state not in {"error", "failed"} and not any(
            str(phase.get("status") or "").casefold() in {"error", "failed"}
            for phase in (processing, publishing)
        ):
            raise FacebookPublishingError("Meta chưa xác nhận lỗi terminal cho job.")
        error = {
            "reason": "META_PROCESSING_FAILED",
            "remote_status": remote_status,
            "diagnostics": diagnostics or {},
            "occurred_at": _now(),
        }
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE facebook_publishing_jobs SET status='FAILED',last_error=?,updated_at=? WHERE id=?",
                (json.dumps(error, ensure_ascii=False, sort_keys=True), _now(), job_id),
            )
        return dict(self.database.connection.execute(
            "SELECT * FROM facebook_publishing_jobs WHERE id=?", (job_id,)
        ).fetchone())

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
            stored_identity = json.loads(job["local_file_identity"] or "{}")
            if stored_identity and self.file_identity(path) != stored_identity:
                raise FacebookPublishingError("File video đã thay đổi sau khi xác nhận; cần xác nhận lại trước khi upload.")
        except FacebookPublishingError as exc:
            self._fail(job_id, str(exc))
            raise
        self.update_progress(job_id, 0, "UPLOADING")
        try:
            result = self.client.upload_video(
                page_id=job["page_id"], file_path=str(path), title=job["title"],
                description=job["description"], progress_callback=lambda value: self._progress(job_id, value, progress),
                checkpoint_callback=lambda checkpoint: self._persist_checkpoint(job_id, checkpoint),
            )
        except Exception as exc:
            self._fail(job_id, str(exc))
            raise FacebookPublishingError(str(exc)) from exc
        upload_id = str(result.get("upload_id") or result.get("id") or "")
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE facebook_publishing_jobs SET remote_upload_id=?, remote_video_id=?,
                   upload_phase='FINISH', status='PROCESSING', updated_at=? WHERE id=?""",
                (upload_id, result.get("remote_video_id") or upload_id, _now(), job_id),
            )
        self.update_progress(job_id, 100, "PUBLISHING")
        try:
            remote = self.client.wait_for_processing(job["page_id"], upload_id)
        except FacebookPublishingPending:
            raise
        except Exception as exc:
            self._fail(job_id, str(exc))
            raise FacebookPublishingError(str(exc)) from exc
        remote_status = remote.get("status")
        if isinstance(remote_status, dict):
            state = str(remote_status.get("video_status") or remote_status.get("status") or "").lower()
            if state in {"processing", "uploading", "transcoding", "pending"}:
                raise FacebookPublishingPending("Meta đang xử lý video; chưa thể xác nhận đã đăng.")
            if state in {"error", "failed"}:
                message = "Meta processing error: " + json.dumps(
                    remote_status, ensure_ascii=False, sort_keys=True
                )
                self._fail(job_id, message)
                raise FacebookPublishingError(message)
        post_id = str(remote.get("post_id") or remote.get("id") or upload_id)
        url = remote.get("url") or remote.get("permalink_url")
        if not post_id or not url:
            raise FacebookPublishingPending("Đã nhận video từ Meta nhưng chưa có permalink xác nhận.")
        url = str(url).strip()
        if not url.startswith(("http://", "https://")):
            url = "https://www.facebook.com" + (url if url.startswith("/") else "/" + url)
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

    def _persist_checkpoint(self, job_id: str, checkpoint: dict[str, Any]) -> None:
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE facebook_publishing_jobs SET upload_session_id=?,remote_video_id=?,
                   upload_start_offset=?,upload_end_offset=?,upload_phase=?,updated_at=? WHERE id=?""",
                (checkpoint.get("upload_session_id"), checkpoint.get("remote_video_id"),
                 checkpoint.get("start_offset"), checkpoint.get("end_offset"),
                 checkpoint.get("upload_phase"), _now(), job_id),
            )

    def _fail(self, job_id: str, message: str) -> None:
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE facebook_publishing_jobs SET status='FAILED',last_error=?,updated_at=? WHERE id=?",
                (message, _now(), job_id),
            )

    @staticmethod
    def file_identity(path: Path) -> dict[str, Any]:
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()}

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

