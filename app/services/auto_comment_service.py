"""Local CSV, planning and persistence helpers for Auto Comment Phase 1."""
from __future__ import annotations

import csv
import io
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


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
    def __init__(self, database) -> None:
        self.database = database

    def list_pages(self) -> list[dict]:
        return [dict(row) for row in self.database.connection.execute(
            """SELECT page_id, MIN(page_name) AS page_name, COUNT(*) AS connection_count
               FROM facebook_pages GROUP BY page_id ORDER BY page_name COLLATE NOCASE"""
        )]

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

    def save_preview(self, values: dict, posts: list[dict], comments_by_post: dict[str, list[str]]) -> str:
        campaign_id = str(uuid.uuid4())
        now = _now()
        with self.database.connection:
            self.database.connection.execute(
                """INSERT INTO auto_comment_campaigns
                   (id,name,page_id,post_start_date,post_end_date,content_type,selection_mode,comments_per_post,prevent_duplicates,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'PREVIEW_READY',?,?)""",
                (campaign_id, values["name"], values["page_id"], values["start_date"], values["end_date"],
                 values["content_type"], values["selection_mode"], values["count"], int(values["prevent_duplicates"]), now, now),
            )
            preview_rows = []
            for post in posts:
                for order, comment in enumerate(comments_by_post.get(post["post_id"], []), start=1):
                    preview_rows.append((str(uuid.uuid4()), campaign_id, post["post_id"], post["post_type"], post["published_at"], comment, order, now))
            self.database.connection.executemany(
                """INSERT INTO auto_comment_preview_items
                   (id,campaign_id,post_id,post_type,published_at,comment_text,selection_order,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""", preview_rows)
        return campaign_id
