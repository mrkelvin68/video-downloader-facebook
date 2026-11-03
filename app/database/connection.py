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
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        try:
            migrate(self.connection)
        except sqlite3.Error:
            self.connection.close()
            raise

    def list_projects(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM projects ORDER BY name COLLATE NOCASE"
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
        return (
            self.connection.execute(
                """SELECT 1 FROM videos
                   WHERE project_id=?
                     AND (download_status NOT IN ('pending', 'waiting')
                          OR local_video_path IS NOT NULL
                          OR thumbnail_path IS NOT NULL)
                   LIMIT 1""",
                (project_id,),
            ).fetchone()
            is None
            and self.connection.execute(
                """SELECT 1 FROM download_jobs
                   WHERE project_id=? AND status IN ('queued', 'downloading')
                   LIMIT 1""",
                (project_id,),
            ).fetchone()
            is None
        )

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
            blocked_video = self.connection.execute(
                """SELECT 1 FROM videos
                   WHERE project_id=?
                     AND (download_status NOT IN ('pending', 'waiting')
                          OR local_video_path IS NOT NULL
                          OR thumbnail_path IS NOT NULL)
                   LIMIT 1""",
                (project_id,),
            ).fetchone()
            active_job = self.connection.execute(
                """SELECT 1 FROM download_jobs
                   WHERE project_id=? AND status IN ('queued', 'downloading')
                   LIMIT 1""",
                (project_id,),
            ).fetchone()
            if blocked_video is not None or active_job is not None:
                raise ImportBlockedError(
                    "Không thể thay thế: dự án có video đang tải/đã tải hoặc có tệp liên kết."
                )
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
                        now,
                        now,
                    )
                )
            self.connection.executemany(
                """INSERT INTO videos
                    (id, project_id, sequence_number, video_code, title, source_url,
                     normalized_url, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                """SELECT sequence_number, title, source_url FROM videos
                   WHERE project_id=? ORDER BY sequence_number""",
                (project_id,),
            )
        )

    def close(self) -> None:
        self.connection.close()
