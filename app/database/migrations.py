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
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
