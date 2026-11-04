"""Local, project-scoped publishing schedule matrices."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from app.database.connection import Database

DEFAULT_PAGES = ("Page 1", "Page 2", "Page 3", "Page 4")
DEFAULT_SLOTS = ("05:00", "10:00", "17:00", "22:00")
NO_REPEAT = "no_repeat"
REPEAT_BETWEEN_PAGES = "repeat_between_pages"
MAX_MATRIX_CELLS = 100_000
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def validate_matrix_configuration(
    name: str,
    pages: Iterable[str],
    slots: Iterable[str],
    start_date: str,
    day_count: int,
    repeat_mode: str,
) -> tuple[str, list[str], list[str], date]:
    name = name.strip()
    if not name or len(name) > 120:
        raise ValueError("Tên ma trận phải có từ 1 đến 120 ký tự.")

    clean_pages = [page.strip() for page in pages]
    if not clean_pages or any(not page or len(page) > 100 for page in clean_pages):
        raise ValueError("Cần ít nhất một Page; tên Page tối đa 100 ký tự.")
    if len(clean_pages) > 50:
        raise ValueError("Mỗi ma trận hỗ trợ tối đa 50 Page.")
    if len({page.casefold() for page in clean_pages}) != len(clean_pages):
        raise ValueError("Tên Page không được trùng nhau.")

    clean_slots = [slot.strip() for slot in slots]
    if not clean_slots or any(not _TIME_PATTERN.fullmatch(slot) for slot in clean_slots):
        raise ValueError("Khung giờ phải đúng định dạng HH:MM, từ 00:00 đến 23:59.")
    if len(clean_slots) > 24:
        raise ValueError("Mỗi ma trận hỗ trợ tối đa 24 khung giờ.")
    if len(set(clean_slots)) != len(clean_slots):
        raise ValueError("Khung giờ không được trùng nhau.")

    if not isinstance(start_date, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", start_date
    ):
        raise ValueError("Ngày bắt đầu phải có định dạng YYYY-MM-DD.")
    try:
        parsed_date = date.fromisoformat(start_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("Ngày bắt đầu không hợp lệ.") from exc
    if not isinstance(day_count, int) or isinstance(day_count, bool) or not 1 <= day_count <= 365:
        raise ValueError("Số ngày tạo lịch phải từ 1 đến 365.")
    if len(clean_pages) * len(clean_slots) * day_count > MAX_MATRIX_CELLS:
        raise ValueError("Cấu hình vượt quá 100.000 ô lịch.")
    if repeat_mode not in (NO_REPEAT, REPEAT_BETWEEN_PAGES):
        raise ValueError("Quy tắc phân phối video không hợp lệ.")
    return name, clean_pages, clean_slots, parsed_date


class MatrixService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @property
    def connection(self) -> sqlite3.Connection:
        return self.database.connection

    def list_matrices(self, project_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                """SELECT m.*,
                          (SELECT COUNT(*) FROM matrix_pages p
                           WHERE p.matrix_id=m.matrix_id AND p.is_active=1) AS page_count,
                          (SELECT COUNT(*) FROM matrix_entries e
                           WHERE e.matrix_id=m.matrix_id) AS entry_count,
                          (SELECT COUNT(*) FROM matrix_entries e
                           WHERE e.matrix_id=m.matrix_id
                             AND e.publish_status='posted') AS posted_count
                   FROM matrices m WHERE m.project_id=?
                   ORDER BY m.created_at, m.name COLLATE NOCASE""",
                (project_id,),
            )
        ]

    def get_matrix(self, matrix_id: str, project_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM matrices WHERE matrix_id=? AND project_id=?",
            (matrix_id, project_id),
        ).fetchone()
        if row is None:
            raise ValueError("Ma trận không tồn tại trong dự án này.")
        return dict(row)

    def get_configuration(self, matrix_id: str, project_id: str) -> dict:
        matrix = self.get_matrix(matrix_id, project_id)
        matrix["pages"] = [
            row["name"]
            for row in self.connection.execute(
                """SELECT name FROM matrix_pages
                   WHERE matrix_id=? AND is_active=1 ORDER BY position""",
                (matrix_id,),
            )
        ]
        matrix["slots"] = [
            row["scheduled_time"]
            for row in self.connection.execute(
                """SELECT scheduled_time FROM matrix_slots
                   WHERE matrix_id=? AND is_active=1 ORDER BY position""",
                (matrix_id,),
            )
        ]
        return matrix

    def list_pages(self, matrix_id: str, project_id: str) -> list[dict]:
        self.get_matrix(matrix_id, project_id)
        return [
            dict(row)
            for row in self.connection.execute(
                """SELECT page_id, name, position FROM matrix_pages
                   WHERE matrix_id=? AND is_active=1 ORDER BY position""",
                (matrix_id,),
            )
        ]

    def create_matrix(
        self,
        project_id: str,
        name: str,
        pages: Iterable[str] = DEFAULT_PAGES,
        slots: Iterable[str] = DEFAULT_SLOTS,
        start_date: str | None = None,
        day_count: int = 5,
        repeat_mode: str = NO_REPEAT,
    ) -> str:
        if self.database.get_project(project_id) is None:
            raise ValueError("Dự án không tồn tại.")
        start_date = start_date or date.today().isoformat()
        name, pages, slots, parsed_date = validate_matrix_configuration(
            name, pages, slots, start_date, day_count, repeat_mode
        )
        matrix_id = str(uuid.uuid4())
        now = self._now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO matrices
                   (matrix_id, project_id, name, start_date, day_count, repeat_mode,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    matrix_id, project_id, name, parsed_date.isoformat(), day_count,
                    repeat_mode, now, now,
                ),
            )
            self._insert_pages(matrix_id, pages, now)
            self._insert_slots(matrix_id, slots, now)
            self._generate_entries(matrix_id, project_id, now)
        return matrix_id

    def rename_matrix(self, matrix_id: str, project_id: str, name: str) -> None:
        name = name.strip()
        if not name or len(name) > 120:
            raise ValueError("Tên ma trận phải có từ 1 đến 120 ký tự.")
        with self.connection:
            self.connection.execute(
                "DELETE FROM matrix_undo_snapshots WHERE matrix_id=?", (matrix_id,)
            )
            cursor = self.connection.execute(
                """UPDATE matrices SET name=?, updated_at=?
                   WHERE matrix_id=? AND project_id=?""",
                (name, self._now(), matrix_id, project_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Ma trận không tồn tại trong dự án này.")

    def delete_matrix(self, matrix_id: str, project_id: str) -> None:
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM matrices WHERE matrix_id=? AND project_id=?",
                (matrix_id, project_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Ma trận không tồn tại trong dự án này.")

    def update_configuration(
        self,
        matrix_id: str,
        project_id: str,
        name: str,
        pages: Iterable[str],
        slots: Iterable[str],
        start_date: str,
        day_count: int,
        repeat_mode: str,
    ) -> None:
        name, pages, slots, parsed_date = validate_matrix_configuration(
            name, pages, slots, start_date, day_count, repeat_mode
        )
        desired_dates = {
            (parsed_date + timedelta(days=offset)).isoformat()
            for offset in range(day_count)
        }
        now = self._now()
        with self.connection:
            matrix = self.connection.execute(
                "SELECT 1 FROM matrices WHERE matrix_id=? AND project_id=?",
                (matrix_id, project_id),
            ).fetchone()
            if matrix is None:
                raise ValueError("Ma trận không tồn tại trong dự án này.")
            posted = self.connection.execute(
                """SELECT p.position, e.scheduled_date, e.scheduled_time
                   FROM matrix_entries e JOIN matrix_pages p ON p.page_id=e.page_id
                   WHERE e.matrix_id=? AND e.publish_status='posted'""",
                (matrix_id,),
            ).fetchall()
            desired_slot_names = set(slots)
            for entry in posted:
                if (
                    entry["position"] >= len(pages)
                    or entry["scheduled_time"] not in desired_slot_names
                    or entry["scheduled_date"] not in desired_dates
                ):
                    raise ValueError(
                        "Không thể bỏ hoặc đổi khung giờ/ngày của ô đã đánh dấu Đã đăng."
                    )

            self._save_undo_snapshot(matrix_id, now)
            self.connection.execute(
                """UPDATE matrices SET name=?, start_date=?, day_count=?,
                   repeat_mode=?, updated_at=? WHERE matrix_id=? AND project_id=?""",
                (
                    name, parsed_date.isoformat(), day_count, repeat_mode, now,
                    matrix_id, project_id,
                ),
            )
            self._sync_pages(matrix_id, pages, now)
            self._sync_slots(matrix_id, slots, now)
            self._generate_entries(matrix_id, project_id, now, snapshot=False)

    def regenerate(self, matrix_id: str, project_id: str) -> None:
        now = self._now()
        with self.connection:
            self.get_matrix(matrix_id, project_id)
            self._save_undo_snapshot(matrix_id, now)
            self._generate_entries(matrix_id, project_id, now, snapshot=False)

    def can_undo(self, matrix_id: str, project_id: str) -> bool:
        return (
            self.connection.execute(
                """SELECT 1 FROM matrix_undo_snapshots s
                   JOIN matrices m ON m.matrix_id=s.matrix_id
                   WHERE s.matrix_id=? AND m.project_id=?""",
                (matrix_id, project_id),
            ).fetchone()
            is not None
        )

    def undo_regeneration(self, matrix_id: str, project_id: str) -> None:
        with self.connection:
            self.get_matrix(matrix_id, project_id)
            snapshot_row = self.connection.execute(
                "SELECT snapshot_json FROM matrix_undo_snapshots WHERE matrix_id=?",
                (matrix_id,),
            ).fetchone()
            if snapshot_row is None:
                raise ValueError("Không có lịch sử để hoàn tác.")
            snapshot = json.loads(snapshot_row["snapshot_json"])
            self.connection.execute(
                "DELETE FROM matrix_entries WHERE matrix_id=?", (matrix_id,)
            )
            self.connection.execute(
                "DELETE FROM matrix_pages WHERE matrix_id=?", (matrix_id,)
            )
            self.connection.execute(
                "DELETE FROM matrix_slots WHERE matrix_id=?", (matrix_id,)
            )
            old_matrix = snapshot["matrix"]
            self.connection.execute(
                """UPDATE matrices SET name=?, start_date=?, day_count=?,
                   repeat_mode=?, updated_at=? WHERE matrix_id=? AND project_id=?""",
                (
                    old_matrix["name"], old_matrix["start_date"], old_matrix["day_count"],
                    old_matrix["repeat_mode"], old_matrix["updated_at"], matrix_id,
                    project_id,
                ),
            )
            self._restore_rows("matrix_pages", snapshot["pages"])
            self._restore_rows("matrix_slots", snapshot["slots"])
            self._restore_rows("matrix_entries", snapshot["entries"])
            self.connection.execute(
                "DELETE FROM matrix_undo_snapshots WHERE matrix_id=?", (matrix_id,)
            )

    def list_entries(self, matrix_id: str, project_id: str) -> list[dict]:
        self.get_matrix(matrix_id, project_id)
        return [
            dict(row)
            for row in self.connection.execute(
                """SELECT e.*, p.name AS page_name, p.position AS page_position,
                          v.video_code, v.title AS video_title,
                          v.local_video_path AS video_path,
                          v.thumbnail_path AS thumbnail_path
                   FROM matrix_entries e
                   JOIN matrix_pages p ON p.page_id=e.page_id
                   LEFT JOIN videos v ON v.id=e.video_id AND v.project_id=e.project_id
                   WHERE e.matrix_id=? AND e.project_id=?
                     AND p.is_active=1
                   ORDER BY e.scheduled_date, e.scheduled_time, p.position""",
                (matrix_id, project_id),
            )
        ]

    def list_project_videos(self, project_id: str) -> list[dict]:
        self.database.get_project(project_id)
        rows = self.connection.execute(
            """SELECT id, video_code, title, source_url, download_status,
                      facebook_uploaded,
                      local_video_path, thumbnail_path, sequence_number
               FROM videos WHERE project_id=? ORDER BY sequence_number""",
            (project_id,),
        )
        return [dict(row) for row in rows]

    def update_entry(
        self,
        matrix_id: str,
        project_id: str,
        entry_id: str,
        video_id: str | None,
        caption: str,
    ) -> None:
        with self.connection:
            entry = self.connection.execute(
                """SELECT e.entry_id, e.page_id, e.scheduled_date, m.repeat_mode
                   FROM matrix_entries e JOIN matrices m ON m.matrix_id=e.matrix_id
                   WHERE e.entry_id=? AND e.matrix_id=? AND e.project_id=?""",
                (entry_id, matrix_id, project_id),
            ).fetchone()
            if entry is None:
                raise ValueError("Ô lịch không tồn tại trong ma trận này.")
            if video_id is not None and self.connection.execute(
                "SELECT 1 FROM videos WHERE id=? AND project_id=?",
                (video_id, project_id),
            ).fetchone() is None:
                raise ValueError("Video không thuộc dự án của ma trận.")
            if video_id is not None:
                scope = (
                    "matrix_id=? AND video_id=? AND entry_id<>?"
                    if entry["repeat_mode"] == NO_REPEAT
                    else (
                        "matrix_id=? AND video_id=? AND page_id=? "
                        "AND scheduled_date=? AND entry_id<>?"
                    )
                )
                values = (
                    (matrix_id, video_id, entry_id)
                    if entry["repeat_mode"] == NO_REPEAT
                    else (
                        matrix_id, video_id, entry["page_id"],
                        entry["scheduled_date"], entry_id,
                    )
                )
                if self.connection.execute(
                    f"SELECT 1 FROM matrix_entries WHERE {scope} LIMIT 1", values
                ).fetchone():
                    raise ValueError(
                        "Video đã được phân bổ trong phạm vi cho phép của ma trận."
                    )
            self.connection.execute(
                "DELETE FROM matrix_undo_snapshots WHERE matrix_id=?", (matrix_id,)
            )
            self.connection.execute(
                """UPDATE matrix_entries SET video_id=?, caption=?, updated_at=?
                   WHERE entry_id=?""",
                (video_id, caption, self._now(), entry_id),
            )

    def set_publish_status(
        self, matrix_id: str, project_id: str, entry_id: str, posted: bool
    ) -> None:
        with self.connection:
            entry = self.connection.execute(
                """SELECT video_id FROM matrix_entries
                   WHERE entry_id=? AND matrix_id=? AND project_id=?""",
                (entry_id, matrix_id, project_id),
            ).fetchone()
            if entry is None:
                raise ValueError("Ô lịch không tồn tại trong ma trận này.")
            if posted and entry["video_id"] is None:
                raise ValueError("Hãy chọn video trước khi đánh dấu Đã đăng.")
            self.connection.execute(
                "DELETE FROM matrix_undo_snapshots WHERE matrix_id=?", (matrix_id,)
            )
            cursor = self.connection.execute(
                """UPDATE matrix_entries SET publish_status=?, updated_at=?
                   WHERE entry_id=? AND matrix_id=? AND project_id=?""",
                (
                    "posted" if posted else "pending", self._now(),
                    entry_id, matrix_id, project_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Ô lịch không tồn tại trong ma trận này.")

    def export_csv(self, matrix_id: str, project_id: str, path: str | Path) -> None:
        rows = self.connection.execute(
            """SELECT e.scheduled_date, e.scheduled_time, p.name,
                      v.video_code, v.title, v.local_video_path, v.thumbnail_path,
                      e.caption, e.publish_status
               FROM matrix_entries e
               JOIN matrix_pages p ON p.page_id=e.page_id AND p.matrix_id=e.matrix_id
               LEFT JOIN videos v ON v.id=e.video_id AND v.project_id=e.project_id
               WHERE e.matrix_id=? AND e.project_id=?
               ORDER BY e.scheduled_date, e.scheduled_time, p.position""",
            (matrix_id, project_id),
        )
        self.get_matrix(matrix_id, project_id)
        with Path(path).open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(
                (
                    "Ngày", "Giờ", "Tên Page", "Mã video", "Title",
                    "Đường dẫn video", "Thumbnail", "Caption", "Trạng thái",
                )
            )
            writer.writerows(
                (
                    row["scheduled_date"],
                    row["scheduled_time"],
                    row["name"],
                    row["video_code"] or "",
                    row["title"] or "",
                    row["local_video_path"] or "",
                    row["thumbnail_path"] or "",
                    row["caption"],
                    "Đã đăng" if row["publish_status"] == "posted" else "Chưa đăng",
                )
                for row in rows
            )

    def _generate_entries(
        self, matrix_id: str, project_id: str, now: str, *, snapshot: bool = True
    ) -> None:
        if snapshot:
            self._save_undo_snapshot(matrix_id, now)
        matrix = self.connection.execute(
            "SELECT * FROM matrices WHERE matrix_id=? AND project_id=?",
            (matrix_id, project_id),
        ).fetchone()
        if matrix is None:
            raise ValueError("Ma trận không tồn tại trong dự án này.")
        pages = self.connection.execute(
            """SELECT page_id, name, position FROM matrix_pages
               WHERE matrix_id=? AND is_active=1 ORDER BY position""",
            (matrix_id,),
        ).fetchall()
        slots = self.connection.execute(
            """SELECT scheduled_time, position FROM matrix_slots
               WHERE matrix_id=? AND is_active=1 ORDER BY position""",
            (matrix_id,),
        ).fetchall()
        posted_rows = self.connection.execute(
            "SELECT * FROM matrix_entries WHERE matrix_id=? AND publish_status='posted'",
            (matrix_id,),
        ).fetchall()
        posted = {
            (
                row["page_id"], row["scheduled_date"], row["scheduled_time"]
            ): dict(row)
            for row in posted_rows
        }
        posted_page_days: dict[tuple[str, str], set[str]] = {}
        posted_videos: set[str] = set()
        for row in posted_rows:
            posted_page_days.setdefault(
                (row["page_id"], row["scheduled_date"]), set()
            )
            if row["video_id"]:
                posted_page_days[(row["page_id"], row["scheduled_date"])].add(
                    row["video_id"]
                )
                posted_videos.add(row["video_id"])

        candidates = self._ordered_videos(project_id)
        candidate_by_id = {
            video["id"]: video for video in candidates
        }
        self.connection.execute(
            """DELETE FROM matrix_entries WHERE matrix_id=?
               AND publish_status!='posted'""",
            (matrix_id,),
        )
        cursor = 0
        video_ids_used: set[str] = set(posted_videos)
        for day_offset in range(matrix["day_count"]):
            scheduled_date = (
                date.fromisoformat(matrix["start_date"]) + timedelta(days=day_offset)
            ).isoformat()
            used_per_page = {
                (page_id, scheduled_date): set(video_ids)
                for (page_id, entry_date), video_ids in posted_page_days.items()
                if entry_date == scheduled_date
            }
            for slot in slots:
                for page in pages:
                    key = (page["page_id"], scheduled_date, slot["scheduled_time"])
                    if key in posted:
                        continue
                    candidate_index = self._next_video_index(
                        candidates,
                        matrix["repeat_mode"],
                        video_ids_used,
                        used_per_page.setdefault((page["page_id"], scheduled_date), set()),
                        cursor,
                    )
                    video_id = (
                        candidates[candidate_index]["id"]
                        if candidate_index is not None
                        else None
                    )
                    if candidate_index is not None:
                        cursor = (candidate_index + 1) % len(candidates)
                        video_ids_used.add(video_id)
                        used_per_page[(page["page_id"], scheduled_date)].add(video_id)
                    self.connection.execute(
                        """INSERT INTO matrix_entries
                           (entry_id, matrix_id, project_id, page_id, video_id,
                            scheduled_date, scheduled_time, caption, publish_status,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                        (
                            str(uuid.uuid4()), matrix_id, project_id, page["page_id"],
                            video_id, scheduled_date, slot["scheduled_time"],
                            candidate_by_id[video_id]["title"] if video_id else "",
                            now, now,
                        ),
                    )

    def _ordered_videos(self, project_id: str) -> list[dict]:
        videos = self.list_project_videos(project_id)
        return sorted(
            videos,
            key=lambda video: (
                not (
                    video["download_status"] == "completed"
                    and video["local_video_path"]
                    and Path(video["local_video_path"]).is_file()
                    and Path(video["local_video_path"]).suffix.casefold() == ".mp4"
                ),
                video["sequence_number"],
            ),
        )

    @staticmethod
    def _next_video_index(
        candidates: list[dict],
        repeat_mode: str,
        used_globally: set[str],
        used_on_page_day: set[str],
        start: int,
    ) -> int | None:
        if not candidates:
            return None
        for offset in range(len(candidates)):
            index = (start + offset) % len(candidates)
            video_id = candidates[index]["id"]
            if video_id in used_on_page_day:
                continue
            if repeat_mode == NO_REPEAT and video_id in used_globally:
                continue
            return index
        return None

    def _insert_pages(self, matrix_id: str, pages: list[str], now: str) -> None:
        self.connection.executemany(
            """INSERT INTO matrix_pages (page_id, matrix_id, name, position, is_active)
               VALUES (?, ?, ?, ?, 1)""",
            [
                (str(uuid.uuid4()), matrix_id, page, position)
                for position, page in enumerate(pages)
            ],
        )

    def _insert_slots(self, matrix_id: str, slots: list[str], now: str) -> None:
        self.connection.executemany(
            """INSERT INTO matrix_slots
               (slot_id, matrix_id, scheduled_time, position, is_active)
               VALUES (?, ?, ?, ?, 1)""",
            [
                (str(uuid.uuid4()), matrix_id, slot, position)
                for position, slot in enumerate(slots)
            ],
        )

    def _sync_pages(self, matrix_id: str, pages: list[str], now: str) -> None:
        existing = self.connection.execute(
            """SELECT * FROM matrix_pages WHERE matrix_id=?
               ORDER BY is_active DESC, position, page_id""",
            (matrix_id,),
        ).fetchall()
        by_position = {row["position"]: row for row in existing}
        self.connection.execute(
            "UPDATE matrix_pages SET is_active=0 WHERE matrix_id=?", (matrix_id,)
        )
        for position, name in enumerate(pages):
            row = by_position.get(position)
            if row is None:
                self.connection.execute(
                    """INSERT INTO matrix_pages
                       (page_id, matrix_id, name, position, is_active)
                       VALUES (?, ?, ?, ?, 1)""",
                    (str(uuid.uuid4()), matrix_id, name, position),
                )
            else:
                self.connection.execute(
                    """UPDATE matrix_pages SET name=?, position=?, is_active=1
                       WHERE page_id=? AND matrix_id=?""",
                    (name, position, row["page_id"], matrix_id),
                )

    def _sync_slots(self, matrix_id: str, slots: list[str], now: str) -> None:
        existing = self.connection.execute(
            """SELECT * FROM matrix_slots WHERE matrix_id=?
               ORDER BY is_active DESC, position, slot_id""",
            (matrix_id,),
        ).fetchall()
        by_position = {row["position"]: row for row in existing}
        self.connection.execute(
            "UPDATE matrix_slots SET is_active=0 WHERE matrix_id=?", (matrix_id,)
        )
        for position, scheduled_time in enumerate(slots):
            row = by_position.get(position)
            if row is None:
                self.connection.execute(
                    """INSERT INTO matrix_slots
                       (slot_id, matrix_id, scheduled_time, position, is_active)
                       VALUES (?, ?, ?, ?, 1)""",
                    (str(uuid.uuid4()), matrix_id, scheduled_time, position),
                )
            else:
                self.connection.execute(
                    """UPDATE matrix_slots SET scheduled_time=?, position=?, is_active=1
                       WHERE slot_id=? AND matrix_id=?""",
                    (scheduled_time, position, row["slot_id"], matrix_id),
                )

    def _save_undo_snapshot(self, matrix_id: str, now: str) -> None:
        matrix = self.connection.execute(
            "SELECT * FROM matrices WHERE matrix_id=?", (matrix_id,)
        ).fetchone()
        if matrix is None:
            raise ValueError("Ma trận không tồn tại.")
        snapshot = {
            "matrix": dict(matrix),
            "pages": self._table_rows("matrix_pages", matrix_id),
            "slots": self._table_rows("matrix_slots", matrix_id),
            "entries": self._table_rows("matrix_entries", matrix_id),
        }
        self.connection.execute(
            """INSERT INTO matrix_undo_snapshots (matrix_id, snapshot_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(matrix_id) DO UPDATE SET
                   snapshot_json=excluded.snapshot_json, updated_at=excluded.updated_at""",
            (matrix_id, json.dumps(snapshot, ensure_ascii=False), now),
        )

    def _table_rows(self, table: str, matrix_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                f"SELECT * FROM {table} WHERE matrix_id=?", (matrix_id,)
            )
        ]

    def _restore_rows(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        columns = tuple(rows[0])
        names = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        self.connection.executemany(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
            [tuple(row[column] for column in columns) for row in rows],
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")
