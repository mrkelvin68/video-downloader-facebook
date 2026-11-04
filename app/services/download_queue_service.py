from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from app.database.connection import Database


ACTIVE_STATUSES = ("queued", "downloading")
MAX_RETRIES = 3
PERMANENT_ERROR_MARKERS = (
    "invalid url",
    "unsupported url",
    "not a valid url",
    "no suitable extractor",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DownloadQueueService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def enqueue(
        self,
        project_id: str,
        video_ids: list[str],
        *,
        run_now: bool = False,
    ) -> list[str]:
        if not video_ids:
            raise ValueError("Chưa chọn video.")
        unique_ids = list(dict.fromkeys(video_ids))
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            if connection.execute(
                "SELECT 1 FROM projects WHERE uuid=?", (project_id,)
            ).fetchone() is None:
                raise ValueError("Dự án không tồn tại.")

            placeholders = ",".join("?" for _ in unique_ids)
            rows = list(
                connection.execute(
                    f"""SELECT id, sequence_number, download_status
                        FROM videos
                        WHERE project_id=? AND id IN ({placeholders})
                        ORDER BY sequence_number""",
                    (project_id, *unique_ids),
                )
            )
            if len(rows) != len(unique_ids):
                raise ValueError("Một hoặc nhiều video không thuộc dự án hiện tại.")
            if any(row["download_status"] == "completed" for row in rows):
                raise ValueError("Không thể thêm video đã tải xong vào hàng đợi.")

            existing_placeholders = ",".join("?" for _ in rows)
            already_active = {
                row["video_id"]: row["status"]
                for row in connection.execute(
                    f"""SELECT video_id, status FROM download_jobs
                        WHERE status IN ('queued', 'downloading')
                          AND video_id IN ({existing_placeholders})""",
                    tuple(row["id"] for row in rows),
                )
            }
            to_add = [row for row in rows if row["id"] not in already_active]
            to_promote = [
                row
                for row in rows
                if run_now
                and already_active.get(row["id"]) == "queued"
            ]
            if not to_add and not to_promote:
                connection.commit()
                return []

            queue_bounds = connection.execute(
                """SELECT COALESCE(MIN(queue_order), 0) AS min_order,
                          COALESCE(MAX(queue_order), 0) AS max_order
                   FROM download_jobs WHERE status IN ('queued', 'downloading')"""
            ).fetchone()
            if run_now:
                ordered_rows = [
                    row
                    for row in rows
                    if row["id"] not in already_active
                    or already_active[row["id"]] == "queued"
                ]
                start_order = queue_bounds["min_order"] - len(ordered_rows)
                priority = 1
            else:
                start_order = queue_bounds["max_order"] + 1
                priority = 0

            created_ids: list[str] = []
            now = _now()
            for index, row in enumerate(ordered_rows if run_now else to_add):
                existing_status = already_active.get(row["id"])
                if existing_status == "queued":
                    connection.execute(
                        """UPDATE download_jobs SET priority=1, queue_order=?
                           WHERE video_id=? AND status='queued'""",
                        (start_order + index, row["id"]),
                    )
                    continue
                if existing_status == "downloading":
                    continue
                job_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO download_jobs
                       (job_id, project_id, video_id, status, priority, queue_order,
                        created_at)
                       VALUES (?, ?, ?, 'queued', ?, ?, ?)""",
                    (
                        job_id,
                        project_id,
                        row["id"],
                        priority,
                        start_order + index,
                        now,
                    ),
                )
                connection.execute(
                    """UPDATE videos SET download_status='waiting', updated_at=?
                       WHERE id=? AND project_id=? AND download_status!='completed'""",
                    (now, row["id"], project_id),
                )
                created_ids.append(job_id)
            connection.commit()
            return created_ids
        except BaseException:
            connection.rollback()
            raise

    def list_jobs(
        self, *, include_finished: bool = True
    ) -> list[sqlite3.Row]:
        where = "" if include_finished else "WHERE j.status IN ('queued', 'downloading')"
        return list(
            self.database.connection.execute(
                f"""SELECT j.*, p.name AS project_name, v.video_code, v.title
                    FROM download_jobs AS j
                    JOIN projects AS p ON p.uuid=j.project_id
                    JOIN videos AS v ON v.id=j.video_id
                    {where}
                    ORDER BY CASE WHEN j.status='downloading' THEN 0
                                  WHEN j.status='queued' THEN 1 ELSE 2 END,
                             j.priority DESC, j.queue_order, j.created_at DESC"""
            )
        )

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        return self.database.connection.execute(
            "SELECT * FROM download_jobs WHERE job_id=?", (job_id,)
        ).fetchone()

    def video_job_info(self, project_id: str) -> dict[str, sqlite3.Row]:
        return {
            row["video_id"]: row
            for row in self.database.connection.execute(
                """SELECT j.* FROM download_jobs AS j
                   WHERE j.project_id=?
                     AND j.rowid=(
                         SELECT newest.rowid FROM download_jobs AS newest
                         WHERE newest.project_id=j.project_id
                           AND newest.video_id=j.video_id
                         ORDER BY newest.created_at DESC, newest.rowid DESC
                         LIMIT 1
                     )""",
                (project_id,),
            )
        }

    def next_queued_job(self) -> sqlite3.Row | None:
        return self.database.connection.execute(
            """SELECT job_id FROM download_jobs
               WHERE status='queued'
               ORDER BY priority DESC, queue_order, created_at
               LIMIT 1"""
        ).fetchone()

    def claim(self, job_id: str) -> sqlite3.Row | None:
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """SELECT j.*, v.video_code
                   FROM download_jobs AS j
                   JOIN videos AS v ON v.id=j.video_id
                   WHERE j.job_id=? AND j.status='queued'""",
                (job_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            cursor = connection.execute(
                """UPDATE download_jobs
                   SET status='downloading', started_at=?, finished_at=NULL,
                       error_message=NULL
                   WHERE job_id=? AND status='queued'""",
                (_now(), job_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
            return row
        except BaseException:
            connection.rollback()
            raise

    def update_progress(
        self,
        job_id: str,
        *,
        progress: float,
        downloaded_bytes: int,
        total_bytes: int | None,
        speed: float | None,
        eta_seconds: int | None = None,
    ) -> None:
        with self.database.connection:
            self.database.connection.execute(
                """UPDATE download_jobs
                   SET progress=?, downloaded_bytes=?, total_bytes=?, speed=?,
                       eta_seconds=?
                   WHERE job_id=? AND status='downloading'""",
                (
                    progress,
                    downloaded_bytes,
                    total_bytes,
                    speed,
                    eta_seconds,
                    job_id,
                ),
            )

    def finish(
        self,
        job_id: str,
        *,
        status: str,
        error_message: str | None = None,
    ) -> None:
        if status not in ("completed", "failed", "interrupted"):
            raise ValueError(f"Trạng thái kết thúc không hợp lệ: {status}")
        now = _now()
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """SELECT project_id, video_id FROM download_jobs
                   WHERE job_id=? AND status='downloading'""",
                (job_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return
            if status == "completed":
                cursor = connection.execute(
                    """UPDATE download_jobs
                       SET status='completed', progress=100, speed=NULL,
                           eta_seconds=NULL,
                           error_message=NULL, finished_at=?
                       WHERE job_id=? AND status='downloading'""",
                    (now, job_id),
                )
            else:
                cursor = connection.execute(
                    """UPDATE download_jobs
                       SET status=?, speed=NULL, eta_seconds=NULL,
                           error_message=?, finished_at=?
                       WHERE job_id=? AND status='downloading'""",
                    (status, error_message, now, job_id),
                )
            if cursor.rowcount == 1:
                video_status = "completed" if status == "completed" else "failed"
                connection.execute(
                    """UPDATE videos SET download_status=?, updated_at=?
                       WHERE id=? AND project_id=?""",
                    (
                        video_status,
                        now,
                        row["video_id"],
                        row["project_id"],
                    ),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def fail_unclaimed(self, job_id: str, message: str) -> bool:
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """SELECT project_id, video_id FROM download_jobs
                   WHERE job_id=? AND status='queued'""",
                (job_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return False
            now = _now()
            connection.execute(
                """UPDATE download_jobs
                   SET status='failed', error_message=?, finished_at=?
                   WHERE job_id=? AND status='queued'""",
                (message, now, job_id),
            )
            connection.execute(
                """UPDATE videos SET download_status='failed', updated_at=?
                   WHERE id=? AND project_id=? AND download_status='waiting'""",
                (now, row["video_id"], row["project_id"]),
            )
            connection.commit()
            return True
        except BaseException:
            connection.rollback()
            raise

    def cancel(self, job_ids: list[str]) -> int:
        if not job_ids:
            return 0
        placeholders = ",".join("?" for _ in job_ids)
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            queued = list(
                connection.execute(
                    f"""SELECT job_id, video_id FROM download_jobs
                        WHERE status='queued' AND job_id IN ({placeholders})""",
                    tuple(job_ids),
                )
            )
            if not queued:
                connection.commit()
                return 0
            queued_ids = [row["job_id"] for row in queued]
            queued_placeholders = ",".join("?" for _ in queued_ids)
            cursor = self.database.connection.execute(
                f"""UPDATE download_jobs
                    SET status='cancelled', finished_at=?, error_message=NULL
                    WHERE status='queued' AND job_id IN ({queued_placeholders})""",
                (_now(), *queued_ids),
            )
            cancelled_video_ids = {row["video_id"] for row in queued}
            for video_id in cancelled_video_ids:
                active = self.database.connection.execute(
                    """SELECT 1 FROM download_jobs
                       WHERE video_id=? AND status IN ('queued', 'downloading')
                       LIMIT 1""",
                    (video_id,),
                ).fetchone()
                if active is None:
                    self.database.connection.execute(
                        """UPDATE videos SET download_status='pending', updated_at=?
                           WHERE id=? AND download_status='waiting'""",
                        (_now(), video_id),
                    )
            connection.commit()
            return cursor.rowcount
        except BaseException:
            connection.rollback()
            raise

    def prioritize(self, job_ids: list[str]) -> int:
        if not job_ids:
            return 0
        placeholders = ",".join("?" for _ in job_ids)
        rows = list(
            self.database.connection.execute(
                f"""SELECT job_id FROM download_jobs
                    WHERE status='queued' AND job_id IN ({placeholders})""",
                tuple(job_ids),
            )
        )
        if not rows:
            return 0
        bounds = self.database.connection.execute(
            """SELECT COALESCE(MIN(queue_order), 0) AS min_order
               FROM download_jobs WHERE status='queued'"""
        ).fetchone()
        start = bounds["min_order"] - len(rows)
        row_set = {row["job_id"] for row in rows}
        ordered_ids = [job_id for job_id in job_ids if job_id in row_set]
        with self.database.connection:
            for index, job_id in enumerate(ordered_ids):
                self.database.connection.execute(
                    """UPDATE download_jobs SET priority=1, queue_order=?
                       WHERE job_id=? AND status='queued'""",
                    (start + index, job_id),
                )
        return len(ordered_ids)

    def retry(self, job_ids: list[str]) -> list[str]:
        if not job_ids:
            return []
        placeholders = ",".join("?" for _ in job_ids)
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            failed = list(
                connection.execute(
                    f"""SELECT j.project_id, j.video_id,
                               (SELECT MAX(history.retry_count)
                                FROM download_jobs AS history
                                WHERE history.video_id=j.video_id) AS retry_count,
                               j.error_message
                        FROM download_jobs AS j
                        WHERE j.status IN ('failed', 'interrupted')
                          AND j.job_id IN ({placeholders})
                        ORDER BY j.queue_order""",
                    tuple(job_ids),
                )
            )
            failed = [
                row
                for row in failed
                if row["retry_count"] < MAX_RETRIES
                and not self._is_permanent_error(row["error_message"])
                and connection.execute(
                    "SELECT download_status FROM videos WHERE id=? AND project_id=?",
                    (row["video_id"], row["project_id"]),
                ).fetchone()["download_status"]
                != "completed"
            ]
            bounds = connection.execute(
                """SELECT COALESCE(MAX(queue_order), 0) AS max_order
                   FROM download_jobs WHERE status IN ('queued', 'downloading')"""
            ).fetchone()
            created: list[str] = []
            now = _now()
            for index, row in enumerate(failed):
                active = connection.execute(
                    """SELECT 1 FROM download_jobs
                       WHERE video_id=? AND status IN ('queued', 'downloading')
                       LIMIT 1""",
                    (row["video_id"],),
                ).fetchone()
                if active:
                    continue
                job_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO download_jobs
                       (job_id, project_id, video_id, status, queue_order,
                        retry_count, created_at)
                       VALUES (?, ?, ?, 'queued', ?, ?, ?)""",
                    (
                        job_id,
                        row["project_id"],
                        row["video_id"],
                        bounds["max_order"] + index + 1,
                        row["retry_count"] + 1,
                        now,
                    ),
                )
                connection.execute(
                    """UPDATE videos SET download_status='waiting', updated_at=?
                       WHERE id=? AND project_id=? AND download_status!='completed'""",
                    (now, row["video_id"], row["project_id"]),
                )
                created.append(job_id)
            connection.commit()
            return created
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _is_permanent_error(message: str | None) -> bool:
        normalized = (message or "").casefold()
        return any(marker in normalized for marker in PERMANENT_ERROR_MARKERS)

    def recover_interrupted(self) -> int:
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            now = _now()
            interrupted = list(
                connection.execute(
                    """SELECT j.job_id, j.project_id, j.video_id,
                              v.download_status AS video_status
                       FROM download_jobs AS j
                       JOIN videos AS v ON v.id=j.video_id
                       WHERE j.status='downloading'"""
                )
            )
            for row in interrupted:
                if row["video_status"] == "completed":
                    connection.execute(
                        """UPDATE download_jobs
                           SET status='completed', progress=100, speed=NULL,
                               error_message=NULL, finished_at=?
                           WHERE job_id=? AND status='downloading'""",
                        (now, row["job_id"]),
                    )
                else:
                    connection.execute(
                        """UPDATE download_jobs
                           SET status='interrupted', speed=NULL,
                               error_message='Ứng dụng đóng khi video đang tải.',
                               finished_at=?
                           WHERE job_id=? AND status='downloading'""",
                        (now, row["job_id"]),
                    )
                    connection.execute(
                        """UPDATE videos SET download_status='failed', updated_at=?
                           WHERE id=? AND project_id=?
                             AND download_status IN ('waiting', 'downloading')""",
                        (now, row["video_id"], row["project_id"]),
                    )
            orphaned = connection.execute(
                """UPDATE videos SET download_status='failed', updated_at=?
                   WHERE download_status='downloading'
                     AND NOT EXISTS (
                         SELECT 1 FROM download_jobs AS j
                         WHERE j.video_id=videos.id AND j.status='downloading'
                     )""",
                (now,),
            ).rowcount
            connection.commit()
            return len(interrupted) + orphaned
        except BaseException:
            connection.rollback()
            raise

    def interrupt_job(self, job_id: str, message: str) -> bool:
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """SELECT project_id, video_id FROM download_jobs
                   WHERE job_id=? AND status='downloading'""",
                (job_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return False
            now = _now()
            connection.execute(
                """UPDATE download_jobs
                   SET status='interrupted', speed=NULL, error_message=?, finished_at=?
                   WHERE job_id=? AND status='downloading'""",
                (message, now, job_id),
            )
            connection.execute(
                """UPDATE videos SET download_status='failed', updated_at=?
                   WHERE id=? AND project_id=?
                     AND download_status IN ('waiting', 'downloading')""",
                (now, row["video_id"], row["project_id"]),
            )
            connection.commit()
            return True
        except BaseException:
            connection.rollback()
            raise
