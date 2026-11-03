import sqlite3


def _create_videos_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE videos (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, sequence_number INTEGER NOT NULL,
            video_code TEXT NOT NULL, title TEXT NOT NULL, source_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL, download_status TEXT NOT NULL DEFAULT 'pending',
            local_video_path TEXT, thumbnail_path TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(uuid) ON DELETE CASCADE,
            UNIQUE(project_id, sequence_number), UNIQUE(project_id, normalized_url),
            UNIQUE(project_id, video_code))"""
    )


def _create_download_jobs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS download_jobs (
            job_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            video_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('queued', 'downloading', 'completed', 'failed',
                           'interrupted', 'cancelled')
            ),
            priority INTEGER NOT NULL DEFAULT 0,
            queue_order INTEGER NOT NULL,
            progress REAL NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
            downloaded_bytes INTEGER NOT NULL DEFAULT 0 CHECK (downloaded_bytes >= 0),
            total_bytes INTEGER,
            speed REAL,
            error_message TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            FOREIGN KEY(project_id) REFERENCES projects(uuid) ON DELETE CASCADE,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
        )"""
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_download_jobs_active_video
           ON download_jobs(video_id)
           WHERE status IN ('queued', 'downloading')"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS ix_download_jobs_queue_order
           ON download_jobs(status, priority DESC, queue_order, created_at)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS ix_download_jobs_project
           ON download_jobs(project_id, created_at DESC)"""
    )


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS projects (
                uuid TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL,
                output_dir TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(videos)")
        }
        if not columns:
            _create_videos_table(connection)
        elif "project_id" not in columns:
            required = {
                "project_uuid", "sequence", "code", "title", "url",
                "normalized_url", "created_at",
            }
            if not required.issubset(columns):
                raise sqlite3.DatabaseError(
                    "Không nhận diện được schema videos cũ; dữ liệu được giữ nguyên."
                )
            legacy_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "videos_legacy" in legacy_tables:
                raise sqlite3.DatabaseError(
                    "Đã tồn tại bảng videos_legacy; dừng migration để tránh ghi đè."
                )
            legacy_count = connection.execute(
                "SELECT COUNT(*) FROM videos"
            ).fetchone()[0]
            connection.execute("ALTER TABLE videos RENAME TO videos_legacy")
            _create_videos_table(connection)
            connection.execute(
                """INSERT INTO videos
                    (id, project_id, sequence_number, video_code, title, source_url,
                     normalized_url, created_at, updated_at)
                    SELECT lower(hex(randomblob(16))), project_uuid, sequence, code,
                           title, url, normalized_url, created_at, created_at
                    FROM videos_legacy"""
            )
            copied_count = connection.execute(
                "SELECT COUNT(*) FROM videos"
            ).fetchone()[0]
            if copied_count != legacy_count:
                raise sqlite3.DatabaseError(
                    "Migration videos không đầy đủ; dữ liệu cũ được giữ nguyên."
                )
            connection.execute("DROP TABLE videos_legacy")
        _create_download_jobs_table(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
