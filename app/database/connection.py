import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from app.core.exceptions import ImportBlockedError, ImportConfirmationRequired
from app.database.migrations import migrate


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10.0)
        self.connection.row_factory = sqlite3.Row
        try:
            migrate(self.connection)
        except sqlite3.Error:
            self.connection.close()
            raise

    def list_projects(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT p.*,
                          (SELECT COUNT(*) FROM matrices m
                           WHERE m.project_id=p.uuid) AS matrix_count
                   FROM projects p ORDER BY p.created_at DESC, p.name COLLATE NOCASE"""
            )
        )

    def get_project(self, project_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM projects WHERE uuid=?", (project_id,)
        ).fetchone()

    def insert_project(self, values: tuple[str, ...]) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?)", values
            )

    def update_project(self, project_id: str, values: tuple[str, ...]) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE projects
                   SET name=?, prefix=?, output_dir=?, notes=?, updated_at=?
                   WHERE uuid=?""",
                (*values, project_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Dự án không tồn tại")

    def list_videos(self, project_id: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM videos WHERE project_id=? ORDER BY sequence_number",
                (project_id,),
            )
        )

    def project_has_videos(self, project_id: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM videos WHERE project_id=? LIMIT 1", (project_id,)
            ).fetchone()
            is not None
        )

    def project_videos_can_be_replaced(self, project_id: str) -> bool:
        return not self.project_replacement_blockers(project_id)

    def project_replacement_blockers(self, project_id: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT v.sequence_number, v.video_code, v.title, v.download_status,
                          v.local_video_path, v.thumbnail_path, j.status AS job_status
                   FROM videos v
                   LEFT JOIN download_jobs j
                     ON j.video_id=v.id AND j.status IN ('queued', 'downloading')
                   WHERE v.project_id=?
                     AND (v.download_status NOT IN ('pending', 'waiting')
                          OR v.local_video_path IS NOT NULL
                          OR v.thumbnail_path IS NOT NULL
                          OR j.status IS NOT NULL)
                   ORDER BY v.sequence_number""",
                (project_id,),
            )
        )

    @staticmethod
    def replacement_blocker_message(blockers: list[sqlite3.Row]) -> str:
        status_labels = {
            "queued": "đang chờ tải",
            "downloading": "đang tải",
            "completed": "đã tải",
            "failed": "đã lỗi tải",
        }
        details = []
        for video in blockers[:5]:
            reasons = []
            if video["job_status"]:
                reasons.append(status_labels.get(video["job_status"], video["job_status"]))
            elif video["download_status"] not in {"pending", "waiting"}:
                reasons.append(status_labels.get(video["download_status"], video["download_status"]))
            if video["local_video_path"]:
                reasons.append("đã có tệp video")
            if video["thumbnail_path"]:
                reasons.append("đã có ảnh bìa")
            details.append(
                f"• {video['video_code']}: "
                f"{video['title']} ({', '.join(reasons)})"
            )
        if len(blockers) > len(details):
            details.append(f"• Và {len(blockers) - len(details)} video khác.")
        return "Không thể thay thế danh sách vì các video sau đang được bảo vệ:\n\n" + "\n".join(details)

    def replace_videos(
        self,
        project_id: str,
        rows: Iterable[dict],
        *,
        replace_confirmed: bool,
    ) -> int:
        prepared_rows = list(rows)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            project = self.connection.execute(
                "SELECT prefix FROM projects WHERE uuid=?", (project_id,)
            ).fetchone()
            if project is None:
                raise ValueError("Dự án không tồn tại")
            existing_video = self.connection.execute(
                "SELECT 1 FROM videos WHERE project_id=? LIMIT 1", (project_id,)
            ).fetchone()
            if existing_video is not None and not replace_confirmed:
                raise ImportConfirmationRequired(
                    "Dự án đã có danh sách video; cần xác nhận thay thế."
                )
            blockers = self.project_replacement_blockers(project_id)
            if blockers:
                raise ImportBlockedError(self.replacement_blocker_message(blockers))
            self.connection.execute(
                "DELETE FROM videos WHERE project_id=?", (project_id,)
            )
            prepared = []
            for row in prepared_rows:
                now = datetime.now(UTC).isoformat(timespec="seconds")
                sequence = row["stt"]
                prepared.append(
                    (
                        str(uuid.uuid4()),
                        project_id,
                        sequence,
                        f"{project['prefix']}-{sequence}",
                        row["ten_video"],
                        row["url"],
                        row["normalized_url"],
                        row.get("platform"),
                        row.get("canonical_url"),
                        row.get("external_video_id"),
                        now,
                        now,
                    )
                )
            self.connection.executemany(
                """INSERT INTO videos
                    (id, project_id, sequence_number, video_code, title, source_url,
                     normalized_url, platform, canonical_url, external_video_id,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                prepared,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return len(prepared_rows)

    def next_video_sequence(self, project_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_sequence "
            "FROM videos WHERE project_id=?",
            (project_id,),
        ).fetchone()
        return int(row["next_sequence"])

    def append_videos(self, project_id: str, rows: Iterable[dict]) -> int:
        prepared_rows = list(rows)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            project = self.connection.execute(
                "SELECT prefix FROM projects WHERE uuid=?", (project_id,)
            ).fetchone()
            if project is None:
                raise ValueError("Dự án không tồn tại")
            start_sequence = self.next_video_sequence(project_id)
            now = datetime.now(UTC).isoformat(timespec="seconds")
            prepared = [
                (
                    str(uuid.uuid4()),
                    project_id,
                    sequence,
                    f"{project['prefix']}-{sequence}",
                    row["ten_video"],
                    row["url"],
                    row["normalized_url"],
                    row.get("platform"),
                    row.get("canonical_url"),
                    row.get("external_video_id"),
                    now,
                    now,
                )
                for sequence, row in enumerate(prepared_rows, start=start_sequence)
            ]
            self.connection.executemany(
                """INSERT INTO videos
                    (id, project_id, sequence_number, video_code, title, source_url,
                     normalized_url, platform, canonical_url, external_video_id,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                prepared,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return len(prepared_rows)

    def export_video_rows(self, project_id: str) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """SELECT title, source_url FROM videos
                   WHERE project_id=? ORDER BY sequence_number""",
                (project_id,),
            )
        )

    def set_video_facebook_uploaded(
        self, project_id: str, video_id: str, uploaded: bool
    ) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE videos
                   SET facebook_uploaded=?, updated_at=?
                   WHERE id=? AND project_id=?""",
                (
                    int(uploaded),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    video_id,
                    project_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Video không tồn tại trong dự án này.")

    def update_video_field(
        self, project_id: str, video_id: str, field: str, value: str
    ) -> None:
        columns = {
            "video_code": "video_code",
            "title": "title",
            "thumbnail_path": "thumbnail_path",
        }
        column = columns.get(field)
        if column is None:
            raise ValueError("Trường video không hợp lệ.")
        with self.connection:
            video = self.connection.execute(
                """SELECT download_status FROM videos
                   WHERE id=? AND project_id=?""",
                (video_id, project_id),
            ).fetchone()
            if video is None:
                raise ValueError("Video không tồn tại trong dự án này.")
            if field in ("video_code", "thumbnail_path"):
                active_job = self.connection.execute(
                    """SELECT 1 FROM download_jobs
                       WHERE video_id=? AND status IN ('queued', 'downloading')
                       LIMIT 1""",
                    (video_id,),
                ).fetchone()
                if (
                    active_job is not None
                    or video["download_status"] == "downloading"
                ):
                    raise ValueError(
                        "Không thể sửa mã hoặc ảnh khi video đang tải hoặc trong hàng đợi."
                    )
            cursor = self.connection.execute(
                f"""UPDATE videos SET {column}=?, updated_at=?
                    WHERE id=? AND project_id=?""",
                (
                    value,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    video_id,
                    project_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Video không tồn tại trong dự án này.")

    def close(self) -> None:
        self.connection.close()
