"""Local CSV, planning and persistence helpers for Auto Comment Phase 1."""
from __future__ import annotations

import csv
import io
import random
import uuid
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.services.credential_service import get_page_token


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CommentItem:
    comment_text: str
    enabled: bool = True
    source_row_id: str | None = None
    sort_order: int = 0


@dataclass(frozen=True)
class CsvImport:
    items: list[CommentItem]
    warnings: list[str]


class AutoCommentValidationError(ValueError):
    pass


class AutoCommentPageAccessError(AutoCommentValidationError):
    """A saved Page is not ready for a read-only Auto Comment preview."""


REQUIRED_READ_PERMISSION = "pages_read_engagement"
REQUIRED_COMMENT_PERMISSION = "pages_manage_engagement"


def parse_comment_csv(raw: bytes | str) -> CsvImport:
    """Parse UTF-8/BOM CSV without modifying the supplied source file."""
    try:
        text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
        reader = csv.DictReader(io.StringIO(text, newline=""))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise AutoCommentValidationError(f"CSV không hợp lệ: {exc}") from exc
    fields = [field.strip().lower() for field in (reader.fieldnames or []) if field]
    if "comment" not in fields:
        raise AutoCommentValidationError("CSV cần có cột comment.")
    items: list[CommentItem] = []
    ids: set[str] = set()
    duplicate_comments: set[str] = set()
    seen_comments: set[str] = set()
    try:
        for number, row in enumerate(reader, start=2):
            if not row or not any((value or "").strip() for value in row.values()):
                continue
            normalized_row = {(key or "").strip().lower(): value for key, value in row.items()}
            comment = normalized_row.get("comment") or ""
            if not comment.strip():
                raise AutoCommentValidationError(f"Dòng {number}: comment không được để trống.")
            source_id = (normalized_row.get("id") or "").strip() or None
            if source_id and source_id in ids:
                raise AutoCommentValidationError(f"Dòng {number}: id bị trùng ({source_id}).")
            if source_id:
                ids.add(source_id)
            enabled_value = (normalized_row.get("enabled") or "1").strip().lower()
            if enabled_value not in {"1", "0", "true", "false", "yes", "no"}:
                raise AutoCommentValidationError(f"Dòng {number}: enabled chỉ nhận 0 hoặc 1.")
            if comment in seen_comments:
                duplicate_comments.add(comment)
            seen_comments.add(comment)
            items.append(CommentItem(comment, enabled_value in {"1", "true", "yes"}, source_id, len(items)))
    except csv.Error as exc:
        raise AutoCommentValidationError(f"CSV không hợp lệ: {exc}") from exc
    if not items:
        raise AutoCommentValidationError("CSV chưa có comment hợp lệ.")
    warnings = [f"Có {len(duplicate_comments)} nội dung comment trùng; dữ liệu vẫn được giữ nguyên."] if duplicate_comments else []
    return CsvImport(items, warnings)


def select_comments(items: list[CommentItem], count: int, mode: str = "random") -> list[str]:
    """Choose distinct text from eligible records; database ordering never affects random mode."""
    if count not in (1, 3):
        raise AutoCommentValidationError("Chỉ hỗ trợ 1 hoặc 3 comment mỗi bài.")
    unique: dict[str, CommentItem] = {}
    for item in items:
        if item.enabled and item.comment_text.strip() and item.comment_text not in unique:
            unique[item.comment_text] = item
    texts = list(unique)
    if len(texts) < count:
        raise AutoCommentValidationError(f"Cần ít nhất {count} comment hợp lệ, không trùng nội dung.")
    if mode == "sequential":
        ordered = sorted(unique.values(), key=lambda item: (item.sort_order, item.source_row_id or "", item.comment_text))
        return [item.comment_text for item in ordered[:count]]
    if mode != "random":
        raise AutoCommentValidationError("Chế độ chọn comment không hợp lệ.")
    return random.SystemRandom().sample(sorted(texts), count)


class AutoCommentService:
    def __init__(self, database, graph_get: Callable[[str, dict[str, str], str], dict[str, Any]] | None = None) -> None:
        self.database = database
        self._graph_get = graph_get or self._live_graph_get

    def list_pages(self) -> list[dict]:
        return [dict(row) for row in self.database.connection.execute(
            """SELECT project_id, page_id, page_name, permissions_json
               FROM facebook_pages ORDER BY page_name COLLATE NOCASE, project_id"""
        )]

    def page_access(self, project_id: str, page_id: str) -> dict[str, Any]:
        row = self.database.connection.execute(
            "SELECT permissions_json, read_access_status FROM facebook_pages WHERE project_id=? AND page_id=?",
            (project_id, page_id),
        ).fetchone()
        if row is None:
            raise AutoCommentPageAccessError("Không tìm thấy Fanpage đã lưu.")
        try:
            permissions = {str(value) for value in json.loads(row["permissions_json"] or "[]")}
        except (TypeError, json.JSONDecodeError):
            permissions = set()
        has_token = bool(get_page_token(project_id, page_id))
        return {
            "has_token": has_token,
            "can_read_posts": REQUIRED_READ_PERMISSION in permissions,
            "can_manage_comments": REQUIRED_COMMENT_PERMISSION in permissions,
            "verified": bool(permissions),
            "read_status": str(row["read_access_status"] or "UNVERIFIED"),
        }

    def _fetch_posts_single_page(
        self, project_id: str, page_id: str, start_date: str, end_date: str, content_type: str,
        *, request: Callable[[str, str, str, str], list[dict[str, Any]]] | None = None,
    ) -> list[dict]:
        """Read Page posts only; this method never creates comments or other Graph objects."""
        access = self.page_access(project_id, page_id)
        if not access["has_token"]:
            raise AutoCommentPageAccessError("Fanpage chưa có Page Access Token; hãy kết nối hoặc đồng bộ lại Fanpage.")
        self._require_verified_read_access(access)
        token = get_page_token(project_id, page_id)
        loader = request or self._graph_posts_single_page
        rows = loader(page_id, token, start_date, end_date)
        wanted = {"all", "posts", "reels"}
        if content_type not in wanted:
            raise AutoCommentValidationError("Loại bài viết không hợp lệ.")
        posts = []
        for row in rows:
            post_type = str(row.get("post_type") or "Post")
            if content_type == "reels" and post_type != "Reel":
                continue
            if content_type == "posts" and post_type == "Reel":
                continue
            post_id = str(row.get("post_id") or row.get("id") or "")
            published_at = str(row.get("published_at") or row.get("created_time") or "")
            if post_id and published_at:
                posts.append({"post_id": post_id, "published_at": published_at, "post_type": post_type})
        return sorted(posts, key=lambda item: (item["published_at"], item["post_id"]), reverse=True)

    @staticmethod
    def _graph_posts_single_page(page_id: str, token: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        params = {
            "fields": "id,created_time,status_type,type",
            "since": start_date, "until": end_date, "limit": "100", "access_token": token,
        }
        request = Request(
            f"https://graph.facebook.com/v26.0/{page_id}/posts?{urlencode(params)}",
            headers={"Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                error_code = str(json.loads(exc.read().decode("utf-8")).get("error", {}).get("code") or "")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                error_code = ""
            if error_code == "190" or exc.code == 401:
                raise AutoCommentPageAccessError("Access Token đã hết hạn hoặc bị thu hồi.") from exc
            if error_code in {"10", "200"} or exc.code == 403:
                raise AutoCommentPageAccessError("Token hợp lệ nhưng thiếu quyền pages_read_engagement để đọc bài viết.") from exc
            detail = f"Graph API code {code}, HTTP {exc.code}" if code else f"HTTP {exc.code}"
            raise AutoCommentPageAccessError(f"Facebook từ chối yêu cầu đọc ({detail}).") from exc
        except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AutoCommentPageAccessError("Không thể kết nối Meta Graph API để tải bài viết.") from exc
        if "error" in payload:
            error_code = str(payload["error"].get("code") or "")
            if error_code == "190":
                raise AutoCommentPageAccessError("Access Token đã hết hạn hoặc bị thu hồi.")
            if error_code in {"10", "200"}:
                raise AutoCommentPageAccessError("Token hợp lệ nhưng thiếu quyền pages_read_engagement để đọc bài viết.")
            detail = f"Graph API code {code}" if code else "lỗi API không xác định"
            raise AutoCommentPageAccessError(f"Facebook từ chối yêu cầu đọc ({detail}).")
        return [
            {"post_id": item.get("id"), "published_at": item.get("created_time"),
             "post_type": "Reel" if item.get("type") == "VIDEO" and item.get("status_type") == "added_video" else "Post"}
            for item in payload.get("data", [])
        ]

    def fetch_posts(
        self, project_id: str, page_id: str, start_date: str, end_date: str, content_type: str,
        *, request: Callable[[str, str, str, str], list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        """Read normal Page posts and Reels; this service never sends a Graph POST."""
        if content_type not in {"all", "posts", "reels"}:
            raise AutoCommentValidationError("Loại bài viết không hợp lệ.")
        access = self.page_access(project_id, page_id)
        if not access["has_token"]:
            raise AutoCommentPageAccessError("Fanpage chưa có Page Access Token; hãy kết nối hoặc đồng bộ lại Fanpage.")
        self._require_verified_read_access(access)
        token = get_page_token(project_id, page_id)
        if request is not None:  # Compatibility seam for existing local callers/tests.
            rows = request(page_id, token, start_date, end_date)
        else:
            self._verify_page_token(page_id, token)
            rows = []
            if content_type in {"all", "posts"}:
                rows.extend(self._read_edge(page_id, token, "posts", start_date, end_date))
            if content_type in {"all", "reels"}:
                rows.extend(self._read_edge(page_id, token, "video_reels", start_date, end_date))
        normalized = self._normalise_posts(rows, start_date, end_date, content_type)
        # This is evidence of Page ownership from the Page edge itself.  It is
        # not an assertion that a Reel/video ID is a comment target.
        for record in normalized:
            record["owner_page_id"] = page_id
        return normalized

    def _verify_page_token(self, page_id: str, token: str) -> None:
        payload = self._graph_get(page_id, {"fields": "id"}, token)
        if str(payload.get("id") or "") != page_id:
            raise AutoCommentPageAccessError("Page Access Token không thuộc Fanpage đang chọn.")

    @staticmethod
    def _require_verified_read_access(access: dict[str, Any]) -> None:
        status = access["read_status"]
        messages = {
            "TOKEN_MISSING": "Fanpage chưa có Page Access Token; hãy kết nối lại.",
            "TOKEN_EXPIRED": "Page Access Token đã hết hạn hoặc bị thu hồi.",
            "WRONG_PAGE": "Page Access Token không thuộc Fanpage đang chọn.",
            "DENIED": "Token hợp lệ nhưng bị từ chối quyền đọc bài viết.",
            "NETWORK_ERROR": "Chưa thể xác minh quyền đọc do lỗi mạng; hãy thử lại.",
            "API_ERROR": "Chưa thể xác minh quyền đọc do lỗi API; hãy thử lại.",
            "UNVERIFIED": "Quyền Fanpage chưa được xác minh; hãy dùng “Kiểm tra Token & quyền”.",
        }
        if status != "GRANTED":
            raise AutoCommentPageAccessError(messages.get(status, messages["UNVERIFIED"]))

    def _read_edge(self, page_id: str, token: str, edge: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        # A Reel ID is not a Post ID.  Request the distinct identifiers returned
        # by Graph so the resolver can validate each candidate read-only.
        fields = "id,video_id,post_id,created_time" if edge == "video_reels" else "id,created_time"
        until = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
        params = {"fields": fields, "since": start_date, "until": until, "limit": "100"}
        rows: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        while True:
            payload = self._graph_get(f"{page_id}/{edge}", params, token)
            data = payload.get("data", [])
            if not isinstance(data, list):
                raise AutoCommentPageAccessError("Phản hồi Facebook không có danh sách bài viết hợp lệ.")
            for item in data:
                if isinstance(item, dict):
                    rows.append({**item, "_edge": edge})
            after = str(payload.get("paging", {}).get("cursors", {}).get("after") or "")
            if not after or after in seen_cursors:
                break
            seen_cursors.add(after)
            params = {**params, "after": after}
        return rows

    @staticmethod
    def _normalise_posts(rows: list[dict[str, Any]], start_date: str, end_date: str, content_type: str) -> list[dict[str, Any]]:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            published_at = str(row.get("published_at") or row.get("created_time") or "")
            try:
                published_date = datetime.fromisoformat(published_at.replace("Z", "+00:00")).date()
            except ValueError:
                continue
            if not start <= published_date <= end:
                continue
            is_reel = row.get("_edge") == "video_reels" or str(row.get("post_type") or "") == "Reel"
            if content_type == "posts" and is_reel or content_type == "reels" and not is_reel:
                continue
            if is_reel:
                reel_id = str(row.get("reel_id") or row.get("id") or "")
                source_post_id = str(row.get("source_post_id") or row.get("post_id") or "") or None
                video_id = str(row.get("video_id") or (row.get("video") or {}).get("id") or "") or None
                if not reel_id:
                    continue
                planner_id = source_post_id or f"reel:{reel_id}"
                record = {"post_id": planner_id, "source_post_id": source_post_id, "video_id": video_id,
                          "reel_id": reel_id, "comment_target_id": source_post_id,
                          "published_at": published_at, "post_type": "Reel"}
            else:
                source_post_id = str(row.get("source_post_id") or row.get("post_id") or row.get("id") or "")
                if not source_post_id:
                    continue
                record = {"post_id": source_post_id, "source_post_id": source_post_id, "video_id": None,
                          "reel_id": None, "comment_target_id": source_post_id,
                          "published_at": published_at, "post_type": "Post"}
            unique.setdefault(record["post_id"], record)
        return sorted(unique.values(), key=lambda item: (item["published_at"], item["post_id"]), reverse=True)

    @staticmethod
    def _live_graph_get(path: str, params: dict[str, str], token: str) -> dict[str, Any]:
        request = Request(
            f"https://graph.facebook.com/v26.0/{path}?{urlencode({**params, 'access_token': token})}",
            headers={"Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                code = str(json.loads(exc.read().decode("utf-8")).get("error", {}).get("code") or "")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                code = ""
            if code == "190" or exc.code == 401:
                raise AutoCommentPageAccessError("Access Token đã hết hạn hoặc bị thu hồi.") from exc
            if code in {"10", "200", "283"} or exc.code == 403:
                raise AutoCommentPageAccessError("Token hợp lệ nhưng thiếu quyền pages_read_engagement để đọc bài viết.") from exc
            detail = f"Graph API code {code}, HTTP {exc.code}" if code else f"HTTP {exc.code}"
            raise AutoCommentPageAccessError(f"Facebook từ chối yêu cầu đọc ({detail}).") from exc
        except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AutoCommentPageAccessError("Không thể kết nối Meta Graph API để tải bài viết.") from exc
        if "error" in payload:
            code = str(payload["error"].get("code") or "")
            if code == "190":
                raise AutoCommentPageAccessError("Access Token đã hết hạn hoặc bị thu hồi.")
            if code in {"10", "200", "283"}:
                raise AutoCommentPageAccessError("Token hợp lệ nhưng thiếu quyền pages_read_engagement để đọc bài viết.")
            detail = f"Graph API code {code}" if code else "lỗi API không xác định"
            raise AutoCommentPageAccessError(f"Facebook từ chối yêu cầu đọc ({detail}).")
        return payload

    def save_library(self, name: str, items: list[CommentItem], library_id: str | None = None) -> str:
        if not name.strip():
            raise AutoCommentValidationError("Hãy nhập tên thư viện comment.")
        if not items:
            raise AutoCommentValidationError("Thư viện chưa có comment.")
        source_ids: set[str] = set()
        for index, item in enumerate(items, start=1):
            if not item.comment_text.strip():
                raise AutoCommentValidationError(f"Dòng {index}: comment không được để trống.")
            if item.source_row_id:
                if item.source_row_id in source_ids:
                    raise AutoCommentValidationError(f"Dòng {index}: id bị trùng ({item.source_row_id}).")
                source_ids.add(item.source_row_id)
        library_id = library_id or str(uuid.uuid4())
        now = _now()
        with self.database.connection:
            self.database.connection.execute(
                """INSERT INTO auto_comment_libraries(id,name,created_at,updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at""",
                (library_id, name.strip(), now, now),
            )
            self.database.connection.execute("DELETE FROM auto_comment_library_items WHERE library_id=?", (library_id,))
            self.database.connection.executemany(
                """INSERT INTO auto_comment_library_items
                   (id,library_id,source_row_id,comment_text,enabled,sort_order,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [(str(uuid.uuid4()), library_id, item.source_row_id, item.comment_text, int(item.enabled), index, now, now)
                 for index, item in enumerate(items)],
            )
        return library_id

    def list_libraries(self) -> list[dict]:
        return [dict(row) for row in self.database.connection.execute(
            "SELECT * FROM auto_comment_libraries ORDER BY updated_at DESC, name COLLATE NOCASE"
        )]

    def load_library(self, library_id: str) -> list[CommentItem]:
        return [CommentItem(str(row["comment_text"]), bool(row["enabled"]), row["source_row_id"], int(row["sort_order"]))
                for row in self.database.connection.execute(
                    "SELECT * FROM auto_comment_library_items WHERE library_id=? ORDER BY sort_order,id", (library_id,))]

    def local_posts(self, page_id: str, start_date: str, end_date: str, content_type: str) -> list[dict]:
        # Publishing jobs have no reliable Reel discriminator. Only completed jobs with a real post ID qualify.
        if content_type == "reels":
            return []
        rows = self.database.connection.execute(
            """SELECT published_post_id AS post_id, updated_at AS published_at, 'Post' AS post_type
               FROM facebook_publishing_jobs
               WHERE page_id=? AND status='PUBLISHED' AND published_post_id IS NOT NULL
                 AND substr(updated_at,1,10) BETWEEN ? AND ?
               ORDER BY updated_at, published_post_id""", (page_id, start_date, end_date),
        )
        return [dict(row) for row in rows]

    def list_campaigns(self) -> list[dict]:
        return [dict(row) for row in self.database.connection.execute(
            "SELECT * FROM auto_comment_campaigns ORDER BY updated_at DESC, name COLLATE NOCASE"
        )]

    def load_campaign_preview(self, campaign_id: str) -> tuple[dict, list[dict]]:
        campaign = self.database.connection.execute(
            "SELECT * FROM auto_comment_campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
        if campaign is None:
            raise AutoCommentValidationError("Không tìm thấy campaign đã lưu.")
        rows = [dict(row) for row in self.database.connection.execute(
            "SELECT * FROM auto_comment_preview_items WHERE campaign_id=? ORDER BY post_id, selection_order", (campaign_id,)
        )]
        return dict(campaign), rows

    def save_preview(self, values: dict, posts: list[dict], comments_by_post: dict[str, list[str]],
                     campaign_id: str | None = None, approved_post_ids: set[str] | None = None) -> str:
        campaign_id = campaign_id or str(uuid.uuid4())
        now = _now()
        with self.database.connection:
            self.database.connection.execute(
                """INSERT INTO auto_comment_campaigns
                   (id,name,page_id,post_start_date,post_end_date,content_type,selection_mode,comments_per_post,prevent_duplicates,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'PREVIEW_READY',?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name,page_id=excluded.page_id,post_start_date=excluded.post_start_date,
                     post_end_date=excluded.post_end_date,content_type=excluded.content_type,
                     selection_mode=excluded.selection_mode,comments_per_post=excluded.comments_per_post,
                     prevent_duplicates=excluded.prevent_duplicates,status=excluded.status,updated_at=excluded.updated_at""",
                (campaign_id, values["name"], values["page_id"], values["start_date"], values["end_date"],
                 values["content_type"], values["selection_mode"], values["count"], int(values["prevent_duplicates"]), now, now),
            )
            published = self.database.connection.execute(
                "SELECT 1 FROM auto_comment_campaign_executions WHERE campaign_id=? AND execution_status IN ('RUNNING','PUBLISHED','UNCERTAIN')",
                (campaign_id,),
            ).fetchone()
            if published:
                raise AutoCommentValidationError("Campaign đã chạy hoặc cần đối soát; không thể thay đổi snapshot đã duyệt.")
            self.database.connection.execute("DELETE FROM auto_comment_preview_items WHERE campaign_id=?", (campaign_id,))
            preview_rows = []
            for post in posts:
                for order, comment in enumerate(comments_by_post.get(post["post_id"], []), start=1):
                    preview_rows.append((str(uuid.uuid4()), campaign_id, post["post_id"], post["post_type"], post["published_at"], comment, order, int(approved_post_ids is None or post["post_id"] in approved_post_ids), now))
            self.database.connection.executemany(
                """INSERT INTO auto_comment_preview_items
                   (id,campaign_id,post_id,post_type,published_at,comment_text,selection_order,selected,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""", preview_rows)
        return campaign_id

    def campaign_execution(self, campaign_id: str) -> list[dict]:
        return [dict(row) for row in self.database.connection.execute(
            """SELECT p.*, COALESCE(e.execution_status, 'PENDING') AS execution_status,
                      e.operation_id,e.remote_comment_id,e.message
               FROM auto_comment_preview_items p
               LEFT JOIN auto_comment_campaign_executions e ON e.preview_item_id=p.id
               WHERE p.campaign_id=? AND p.selected=1 ORDER BY p.selection_order,p.post_id""", (campaign_id,)
        )]

    def begin_execution(self, campaign_id: str, preview_item_id: str, operation_id: str) -> bool:
        now = _now()
        with self.database.connection:
            existing = self.database.connection.execute(
                "SELECT execution_status FROM auto_comment_campaign_executions WHERE preview_item_id=?", (preview_item_id,)
            ).fetchone()
            if existing is not None:
                return False
            self.database.connection.execute(
                """INSERT INTO auto_comment_campaign_executions
                   (id,campaign_id,preview_item_id,execution_status,operation_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)""", (str(uuid.uuid4()), campaign_id, preview_item_id, 'RUNNING', operation_id, now, now)
            )
        return True

    def finish_execution(self, preview_item_id: str, result: dict) -> None:
        state = {'SUCCESS': 'PUBLISHED', 'UNCERTAIN': 'UNCERTAIN'}.get(result.get('status'), 'FAILED')
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE auto_comment_campaign_executions SET execution_status=?,remote_comment_id=?,message=?,updated_at=?
                   WHERE preview_item_id=?""",
                (state, result.get('comment_id'), result.get('message'), _now(), preview_item_id),
            )
