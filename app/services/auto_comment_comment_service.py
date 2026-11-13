"""Safe target resolution and explicitly-confirmed single-comment operations.

This module deliberately has no batch or scheduler API.  Target discovery only
uses Graph GET requests; the sole POST method requires an explicit confirmation
flag supplied by the UI after its confirmation dialog has been accepted.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.services.auto_comment_service import (
    AutoCommentPageAccessError, AutoCommentService, AutoCommentValidationError,
)
from app.services.credential_service import get_page_token


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class AutoCommentTargetError(AutoCommentValidationError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class AutoCommentCommentService:
    """Resolve Page-owned comment targets and persist a non-retryable history."""

    def __init__(
        self, database, graph_get: Callable[[str, dict[str, str], str], dict[str, Any]] | None = None,
        graph_post: Callable[[str, dict[str, str], str], dict[str, Any]] | None = None,
    ) -> None:
        self.database = database
        self._graph_get = graph_get or AutoCommentService._live_graph_get
        self._graph_post = graph_post or self._live_graph_post

    def resolve_target(self, project_id: str, page_id: str, source: dict[str, Any]) -> dict[str, Any]:
        """Use GET only to establish an actual, Page-owned comment endpoint.

        IDs are candidates, never transformations of one another.  A Reel with
        no verified candidate remains TARGET_UNVERIFIED rather than being treated
        as a Post.
        """
        source_id = str(source.get("post_id") or "")
        if not source_id:
            raise AutoCommentTargetError("TARGET_UNVERIFIED", "Bài viết không có ID nguồn để xác minh.")
        now = _now()
        try:
            access = AutoCommentService(self.database).page_access(project_id, page_id)
            if not access["has_token"]:
                raise AutoCommentTargetError("TOKEN_MISSING", "Fanpage chưa có Page Access Token.")
            AutoCommentService._require_verified_read_access(access)
            token = get_page_token(project_id, page_id)
            identity = self._graph_get(page_id, {"fields": "id"}, token)
            if str(identity.get("id") or "") != page_id:
                raise AutoCommentTargetError("WRONG_PAGE", "Page Access Token không thuộc Fanpage đang chọn.")
            candidates = self._candidates(source)
            if not candidates:
                raise AutoCommentTargetError("TARGET_UNVERIFIED", "Reel chưa có target comment được Facebook xác minh.")
            last_error: AutoCommentTargetError | None = None
            for candidate in candidates:
                try:
                    is_post_candidate = candidate in {str(source.get("comment_target_id") or ""), str(source.get("source_post_id") or "")}
                    metadata = self._graph_get(candidate, {"fields": "id,from{id}" if is_post_candidate else "id"}, token)
                    owner_id = str((metadata.get("from") or {}).get("id") or "")
                    edge_owner_id = str(source.get("owner_page_id") or "")
                    # Video/Reel metadata does not consistently expose `from`.
                    # In that case the prior Page-edge response is ownership
                    # evidence, while the comments GET below still proves the
                    # candidate endpoint is readable.  Neither step equates IDs.
                    if owner_id and owner_id != page_id:
                        raise AutoCommentTargetError("TARGET_UNSUPPORTED", "Target không thuộc Fanpage đang chọn.")
                    if not owner_id and edge_owner_id != page_id:
                        raise AutoCommentTargetError("TARGET_UNVERIFIED", "Facebook không trả về bằng chứng Fanpage sở hữu target.")
                    # A GET on comments is read-only evidence that the object is
                    # comment-addressable.  It does not create a comment.
                    self._graph_get(f"{candidate}/comments", {"limit": "1", "summary": "true"}, token)
                    result = {"source_id": source_id, "target_id": candidate,
                              "target_type": "REEL" if source.get("post_type") == "Reel" else "POST",
                              "status": "READY", "evidence": {"owner_id": owner_id or edge_owner_id, "checked": "comments"}}
                    self._save_target(project_id, page_id, result, now)
                    return result
                except AutoCommentTargetError as exc:
                    last_error = exc
                except AutoCommentPageAccessError as exc:
                    last_error = AutoCommentTargetError(self._category_for_error(str(exc)), str(exc))
            assert last_error is not None
            raise last_error
        except AutoCommentTargetError as exc:
            result = {"source_id": source_id, "target_id": None, "target_type": None,
                      "status": exc.category, "evidence": {"message": str(exc)}}
            self._save_target(project_id, page_id, result, now)
            return result
        except AutoCommentPageAccessError as exc:
            category = self._category_for_error(str(exc))
            result = {"source_id": source_id, "target_id": None, "target_type": None,
                      "status": category, "evidence": {"message": str(exc)}}
            self._save_target(project_id, page_id, result, now)
            return result

    @staticmethod
    def _candidates(source: dict[str, Any]) -> list[str]:
        # The order is deliberately explicit.  Never manufacture a post ID from
        # video/reel data, and never assume those identifiers mean the same thing.
        values = (source.get("comment_target_id"), source.get("source_post_id"),
                  source.get("video_id"), source.get("reel_id"))
        unique: list[str] = []
        for value in values:
            value = str(value or "")
            if value and value not in unique:
                unique.append(value)
        return unique

    @staticmethod
    def _category_for_error(message: str) -> str:
        text = message.casefold()
        if "hết hạn" in text or "thu hồi" in text:
            return "TOKEN_EXPIRED"
        if "không thuộc fanpage" in text:
            return "WRONG_PAGE"
        if "graph api code 100" in text or "graph api code 12" in text:
            return "TARGET_UNSUPPORTED"
        if "thiếu quyền" in text or "từ chối" in text:
            return "PERMISSION_DENIED"
        if "kết nối" in text or "mạng" in text:
            return "ERROR"
        return "TARGET_UNVERIFIED"

    def _save_target(self, project_id: str, page_id: str, result: dict[str, Any], now: str) -> None:
        with self.database.connection:
            self.database.connection.execute(
                """INSERT INTO auto_comment_targets
                   (id,project_id,page_id,source_id,target_id,target_type,status,evidence_json,checked_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id,page_id,source_id) DO UPDATE SET
                   target_id=excluded.target_id,target_type=excluded.target_type,status=excluded.status,
                   evidence_json=excluded.evidence_json,checked_at=excluded.checked_at,updated_at=excluded.updated_at""",
                (str(uuid.uuid4()), project_id, page_id, result["source_id"], result["target_id"],
                 result["target_type"], result["status"], json.dumps(result["evidence"], ensure_ascii=False), now, now),
            )

    def publish_one(self, project_id: str, page_id: str, source_id: str, target_id: str,
                    comment_text: str, *, confirmed: bool, operation_id: str | None = None) -> dict[str, Any]:
        """Send exactly one comment after explicit confirmation; never auto-retry."""
        if not confirmed:
            raise AutoCommentValidationError("Cần xác nhận rõ ràng trước khi gửi comment.")
        if not comment_text.strip():
            raise AutoCommentValidationError("Nội dung comment không được để trống.")
        operation_id = operation_id or str(uuid.uuid4())
        existing = self.database.connection.execute(
            "SELECT status,remote_comment_id FROM auto_comment_submission_attempts WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if existing is not None:
            raise AutoCommentValidationError("Operation đã tồn tại; không tự gửi lại comment.")
        now = _now()
        with self.database.connection:
            self.database.connection.execute(
                """INSERT INTO auto_comment_submission_attempts
                   (id,operation_id,project_id,page_id,source_id,target_id,comment_text,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,'PENDING',?,?)""",
                (str(uuid.uuid4()), operation_id, project_id, page_id, source_id, target_id, comment_text, now, now),
            )
        try:
            token = get_page_token(project_id, page_id)
            if not token:
                raise AutoCommentTargetError("TOKEN_MISSING", "Fanpage chưa có Page Access Token.")
            payload = self._graph_post(f"{target_id}/comments", {"message": comment_text}, token)
            remote_id = str(payload.get("id") or "")
            if not remote_id:
                raise AutoCommentTargetError("UNCERTAIN", "Facebook không trả về comment ID; không tự gửi lại.")
            verification = "READ_BACK_UNAVAILABLE"
            try:
                read_back = self._graph_get(remote_id, {"fields": "id"}, token)
                if str(read_back.get("id") or "") == remote_id:
                    verification = "READ_BACK"
            except (AutoCommentPageAccessError, AutoCommentTargetError):
                pass
            self._finish(operation_id, "SUCCESS", remote_id, None, None, verification)
            return {"operation_id": operation_id, "status": "SUCCESS", "comment_id": remote_id,
                    "verification_status": verification}
        except AutoCommentTargetError as exc:
            status = "UNCERTAIN" if exc.category in {"UNCERTAIN", "NETWORK_ERROR"} else "FAILED"
            self._finish(operation_id, status, None, exc.category, str(exc), "NOT_ATTEMPTED")
            return {"operation_id": operation_id, "status": status, "error_category": exc.category, "message": str(exc)}

    def _finish(self, operation_id: str, status: str, remote_id: str | None, category: str | None,
                message: str | None, verification: str) -> None:
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE auto_comment_submission_attempts SET status=?,remote_comment_id=?,error_category=?,
                   error_message=?,verification_status=?,updated_at=? WHERE operation_id=?""",
                (status, remote_id, category, message, verification, _now(), operation_id),
            )

    @staticmethod
    def _live_graph_post(path: str, data: dict[str, str], token: str) -> dict[str, Any]:
        request = Request(f"https://graph.facebook.com/v26.0/{path}",
                          data=urlencode({**data, "access_token": token}).encode("utf-8"),
                          headers={"Accept": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                code = str(json.loads(exc.read().decode("utf-8")).get("error", {}).get("code") or "")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                code = ""
            if code == "190" or exc.code == 401:
                raise AutoCommentTargetError("TOKEN_EXPIRED", "Page Access Token đã hết hạn hoặc bị thu hồi.") from exc
            if code == "368":
                raise AutoCommentTargetError("RATE_LIMIT", "Facebook tạm chặn thao tác (368); hãy dừng và kiểm tra lịch sử.") from exc
            if code in {"10", "200", "283"} or exc.code == 403:
                raise AutoCommentTargetError("PERMISSION_DENIED", "Token không có quyền quản lý comment cho target này.") from exc
            raise AutoCommentTargetError("API_ERROR", f"Facebook từ chối gửi comment (HTTP {exc.code}).") from exc
        except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AutoCommentTargetError("NETWORK_ERROR", "Không chắc Facebook đã nhận comment do lỗi mạng; không tự gửi lại.") from exc
        if "error" in payload:
            code = str(payload["error"].get("code") or "")
            if code == "368":
                raise AutoCommentTargetError("RATE_LIMIT", "Facebook tạm chặn thao tác (368); hãy dừng và kiểm tra lịch sử.")
            raise AutoCommentTargetError("API_ERROR", "Facebook không xác nhận comment đã được tạo.")
        return payload
