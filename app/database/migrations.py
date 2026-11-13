import sqlite3


def _create_videos_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE videos (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, sequence_number INTEGER NOT NULL,
            video_code TEXT NOT NULL, title TEXT NOT NULL, source_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL, download_status TEXT NOT NULL DEFAULT 'pending',
            local_video_path TEXT, thumbnail_path TEXT,
            platform TEXT, canonical_url TEXT, external_video_id TEXT,
            author_name TEXT, duration_seconds REAL, resolution TEXT,
            facebook_uploaded INTEGER NOT NULL DEFAULT 0
                CHECK(facebook_uploaded IN (0, 1)),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(uuid) ON DELETE CASCADE,
            UNIQUE(project_id, sequence_number), UNIQUE(project_id, normalized_url),
            UNIQUE(project_id, video_code))"""
    )


def _create_global_video_url_triggers(connection: sqlite3.Connection) -> None:
    """Reject new cross-project URL duplicates without invalidating legacy rows."""
    for event, suffix in (("INSERT", "insert"), ("UPDATE OF normalized_url", "update")):
        connection.execute(
            f"""CREATE TRIGGER IF NOT EXISTS trg_videos_global_normalized_url_{suffix}
                BEFORE {event} ON videos
                FOR EACH ROW
                WHEN EXISTS (
                    SELECT 1 FROM videos
                    WHERE normalized_url=NEW.normalized_url
                      AND project_id<>NEW.project_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'Video URL already belongs to another project');
                END"""
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
            eta_seconds INTEGER,
            retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
            error_message TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            FOREIGN KEY(project_id) REFERENCES projects(uuid) ON DELETE CASCADE,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
        )"""
    )
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(download_jobs)")
    }
    if "eta_seconds" not in columns:
        connection.execute("ALTER TABLE download_jobs ADD COLUMN eta_seconds INTEGER")
    if "retry_count" not in columns:
        connection.execute(
            """ALTER TABLE download_jobs
               ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0)"""
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


def _create_matrices_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS matrices (
            matrix_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            start_date TEXT NOT NULL,
            day_count INTEGER NOT NULL CHECK(day_count BETWEEN 1 AND 365),
            repeat_mode TEXT NOT NULL CHECK(
                repeat_mode IN ('no_repeat', 'repeat_between_pages')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(uuid) ON DELETE CASCADE,
            UNIQUE(matrix_id, project_id)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS matrix_pages (
            page_id TEXT PRIMARY KEY,
            matrix_id TEXT NOT NULL,
            name TEXT NOT NULL,
            position INTEGER NOT NULL CHECK(position >= 0),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            FOREIGN KEY(matrix_id) REFERENCES matrices(matrix_id) ON DELETE CASCADE,
            UNIQUE(page_id, matrix_id)
        )"""
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_matrix_pages_active_name
           ON matrix_pages(matrix_id, name COLLATE NOCASE) WHERE is_active=1"""
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_matrix_pages_active_position
           ON matrix_pages(matrix_id, position) WHERE is_active=1"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS matrix_slots (
            slot_id TEXT PRIMARY KEY,
            matrix_id TEXT NOT NULL,
            scheduled_time TEXT NOT NULL,
            position INTEGER NOT NULL CHECK(position >= 0),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            FOREIGN KEY(matrix_id) REFERENCES matrices(matrix_id) ON DELETE CASCADE,
            UNIQUE(slot_id, matrix_id)
        )"""
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_matrix_slots_active_time
           ON matrix_slots(matrix_id, scheduled_time) WHERE is_active=1"""
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_matrix_slots_active_position
           ON matrix_slots(matrix_id, position) WHERE is_active=1"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS matrix_entries (
            entry_id TEXT PRIMARY KEY,
            matrix_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            page_id TEXT NOT NULL,
            video_id TEXT,
            scheduled_date TEXT NOT NULL,
            scheduled_time TEXT NOT NULL,
            caption TEXT NOT NULL DEFAULT '',
            publish_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(publish_status IN ('pending', 'posted')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(matrix_id, project_id)
                REFERENCES matrices(matrix_id, project_id) ON DELETE CASCADE,
            FOREIGN KEY(page_id, matrix_id)
                REFERENCES matrix_pages(page_id, matrix_id) ON DELETE CASCADE,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE SET NULL,
            UNIQUE(matrix_id, page_id, scheduled_date, scheduled_time)
        )"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS tr_matrix_entry_video_project_insert
           BEFORE INSERT ON matrix_entries
           WHEN NEW.video_id IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM videos
               WHERE id=NEW.video_id AND project_id=NEW.project_id
           )
           BEGIN
               SELECT RAISE(ABORT, 'matrix video must belong to its project');
           END"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS tr_matrix_entry_video_project_update
           BEFORE UPDATE OF video_id, project_id ON matrix_entries
           WHEN NEW.video_id IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM videos
               WHERE id=NEW.video_id AND project_id=NEW.project_id
           )
           BEGIN
               SELECT RAISE(ABORT, 'matrix video must belong to its project');
           END"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS tr_matrix_video_delete_clear_undo
           AFTER DELETE ON videos
           BEGIN
               DELETE FROM matrix_undo_snapshots
               WHERE matrix_id IN (
                   SELECT matrix_id FROM matrices WHERE project_id=OLD.project_id
               );
           END"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS ix_matrix_entries_schedule
           ON matrix_entries(matrix_id, scheduled_date, scheduled_time, page_id)"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS matrix_undo_snapshots (
            matrix_id TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(matrix_id) REFERENCES matrices(matrix_id) ON DELETE CASCADE
        )"""
    )


def _create_discovered_pages_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
    CREATE TABLE IF NOT EXISTS discovered_pages (
        page_id TEXT PRIMARY KEY,
        page_name TEXT NOT NULL,
        page_url TEXT NOT NULL,
        country TEXT NOT NULL,
        language TEXT NOT NULL,
        topic TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL,
        discovered_at TEXT NOT NULL,
        is_saved INTEGER NOT NULL DEFAULT 1,
        UNIQUE(page_url)
    )
    """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(discovered_pages)")}
    if "is_saved" not in columns:
        connection.execute(
            "ALTER TABLE discovered_pages ADD COLUMN is_saved INTEGER NOT NULL DEFAULT 1"
        )


def _create_reels_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
    CREATE TABLE IF NOT EXISTS reels (
        reel_id TEXT PRIMARY KEY,
        reel_url TEXT NOT NULL,
        page_id TEXT NOT NULL,
        page_name TEXT NOT NULL,
        page_url TEXT NOT NULL,
        caption TEXT NOT NULL DEFAULT '',
        views INTEGER,
        likes INTEGER,
        comments INTEGER,
        shares INTEGER,
        published_at TEXT,
        collected_at TEXT NOT NULL,
        country TEXT,
        language TEXT,
        topic TEXT NOT NULL DEFAULT '',
        viral_score REAL DEFAULT 0,
        data_source TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'collected',
        updated_at TEXT NOT NULL,
        FOREIGN KEY(page_id) REFERENCES discovered_pages(page_id),
        UNIQUE(reel_id),
        UNIQUE(reel_url)
    )
    """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(reels)")}
    if "updated_at" not in columns:
        connection.execute("ALTER TABLE reels ADD COLUMN updated_at TEXT")
        connection.execute("UPDATE reels SET updated_at=collected_at WHERE updated_at IS NULL")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_reels_filters ON reels(country, language, topic, published_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_reels_rank ON reels(viral_score DESC, views DESC)"
    )


def _create_reel_metrics_history_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
    CREATE TABLE IF NOT EXISTS reel_metrics_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reel_id TEXT NOT NULL,
        views INTEGER,
        likes INTEGER,
        comments INTEGER,
        shares INTEGER,
        measured_at TEXT NOT NULL,
        FOREIGN KEY(reel_id) REFERENCES reels(reel_id) ON DELETE CASCADE
    )
    """
    )


def _create_collection_jobs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
    CREATE TABLE IF NOT EXISTS collection_jobs (
        job_id TEXT PRIMARY KEY,
        project_id TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
        pages_discovered INTEGER NOT NULL DEFAULT 0,
        reels_collected INTEGER NOT NULL DEFAULT 0,
        reels_qualified INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(project_id) REFERENCES projects(uuid) ON DELETE CASCADE
    )
    """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(collection_jobs)")}
    if "schedule" not in columns:
        connection.execute("ALTER TABLE collection_jobs ADD COLUMN schedule TEXT NOT NULL DEFAULT 'manual'")
    if "last_success_at" not in columns:
        connection.execute("ALTER TABLE collection_jobs ADD COLUMN last_success_at TEXT")


def _create_export_history_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
    CREATE TABLE IF NOT EXISTS export_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        export_type TEXT NOT NULL CHECK(export_type IN ('csv', 'xlsx', 'google_sheets')),
        export_path TEXT,
        row_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(job_id) REFERENCES collection_jobs(job_id) ON DELETE CASCADE
    )
    """
    )


def _create_provider_configuration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
    CREATE TABLE IF NOT EXISTS provider_configuration (
        provider_name TEXT PRIMARY KEY,
        config_json TEXT NOT NULL DEFAULT '{}',
        last_configured_at TEXT,
        is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
        UNIQUE(provider_name)
    )
    """
    )


def _create_facebook_publishing_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS facebook_pages (
            project_id TEXT NOT NULL,
            page_id TEXT NOT NULL,
            page_name TEXT NOT NULL,
            page_url TEXT NOT NULL DEFAULT '',
            permissions_json TEXT NOT NULL DEFAULT '[]',
            read_access_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
            read_access_verified_at TEXT,
            is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(project_id, page_id),
            FOREIGN KEY(project_id) REFERENCES projects(uuid) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_facebook_default_page
           ON facebook_pages(project_id) WHERE is_default=1"""
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS facebook_publishing_jobs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            video_id TEXT NOT NULL,
            page_id TEXT NOT NULL,
            local_file_path TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            scheduled_at TEXT,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            status TEXT NOT NULL CHECK(status IN (
                'PENDING','SCHEDULED','PREPARING','UPLOADING','PROCESSING',
                'PUBLISHING','PUBLISHED','FAILED','PAUSED','CANCELLED')),
            progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 100),
            retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
            remote_upload_id TEXT,
            published_post_id TEXT,
            published_url TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(uuid) ON DELETE CASCADE,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id, page_id) REFERENCES facebook_pages(project_id, page_id)
        )
        """
    )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_facebook_active_video_page
           ON facebook_publishing_jobs(project_id, video_id, page_id)
           WHERE status NOT IN ('FAILED','CANCELLED')"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS ix_facebook_publish_schedule
           ON facebook_publishing_jobs(project_id, status, scheduled_at)"""
    )
    page_columns = {row["name"] for row in connection.execute("PRAGMA table_info(facebook_pages)")}
    for name, definition in (
        ("read_access_status", "TEXT NOT NULL DEFAULT 'UNVERIFIED'"),
        ("read_access_verified_at", "TEXT"),
    ):
        if name not in page_columns:
            connection.execute(f"ALTER TABLE facebook_pages ADD COLUMN {name} {definition}")
    job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(facebook_publishing_jobs)")}
    for name, definition in (
        ("upload_session_id", "TEXT"),
        ("remote_video_id", "TEXT"),
        ("upload_start_offset", "INTEGER"),
        ("upload_end_offset", "INTEGER"),
        ("upload_phase", "TEXT"),
        ("local_file_identity", "TEXT"),
        ("last_status_check", "TEXT"),
        ("last_status_check_at", "TEXT"),
        ("reconciliation_state", "TEXT"),
        ("retry_of_job_id", "TEXT"),
    ):
        if name not in job_columns:
            connection.execute(f"ALTER TABLE facebook_publishing_jobs ADD COLUMN {name} {definition}")
    connection.execute("DROP INDEX IF EXISTS uq_facebook_active_video_page")
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS uq_facebook_active_video_page
           ON facebook_publishing_jobs(project_id, video_id, page_id)
           WHERE status NOT IN ('FAILED','CANCELLED')
             AND COALESCE(reconciliation_state, '') <> 'UNRESOLVED'"""
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS facebook_drafts (
            project_id TEXT NOT NULL,
            video_id TEXT NOT NULL,
            page_id TEXT,
            facebook_title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            hashtags TEXT NOT NULL DEFAULT '',
            preparation_status TEXT NOT NULL DEFAULT 'NOT_PREPARED',
            preparation_json TEXT NOT NULL DEFAULT '{}',
            last_error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(project_id, video_id),
            FOREIGN KEY(project_id) REFERENCES projects(uuid) ON DELETE CASCADE,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id, page_id) REFERENCES facebook_pages(project_id, page_id)
        )
        """
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS ix_facebook_drafts_project
           ON facebook_drafts(project_id, updated_at DESC)"""
    )


def _create_auto_comment_tables(connection: sqlite3.Connection) -> None:
    """Local-only data for the Auto Comment planning screen (no credentials)."""
    connection.execute("""CREATE TABLE IF NOT EXISTS auto_comment_libraries (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS auto_comment_library_items (
        id TEXT PRIMARY KEY, library_id TEXT NOT NULL, source_row_id TEXT,
        comment_text TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        sort_order INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        FOREIGN KEY(library_id) REFERENCES auto_comment_libraries(id) ON DELETE CASCADE)""")
    connection.execute("""CREATE INDEX IF NOT EXISTS ix_auto_comment_library_items_order
        ON auto_comment_library_items(library_id, sort_order, id)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS auto_comment_campaigns (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, page_id TEXT NOT NULL,
        post_start_date TEXT NOT NULL, post_end_date TEXT NOT NULL, content_type TEXT NOT NULL,
        selection_mode TEXT NOT NULL, comments_per_post INTEGER NOT NULL CHECK(comments_per_post IN (1,3)),
        prevent_duplicates INTEGER NOT NULL DEFAULT 1 CHECK(prevent_duplicates IN (0,1)),
        status TEXT NOT NULL CHECK(status IN ('DRAFT','PREVIEW_READY','INVALID')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS auto_comment_preview_items (
        id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, post_id TEXT NOT NULL,
        post_type TEXT NOT NULL, published_at TEXT NOT NULL, comment_text TEXT NOT NULL,
        selection_order INTEGER NOT NULL, selected INTEGER NOT NULL DEFAULT 1 CHECK(selected IN (0,1)),
        created_at TEXT NOT NULL,
        FOREIGN KEY(campaign_id) REFERENCES auto_comment_campaigns(id) ON DELETE CASCADE)""")
    connection.execute("""CREATE INDEX IF NOT EXISTS ix_auto_comment_preview_campaign
        ON auto_comment_preview_items(campaign_id, post_id, selection_order)""")
    # These records are intentionally separate from previews: resolving a target is
    # evidence from a read-only Graph request, while a submission is an auditable
    # one-shot operation.  No credential material is stored here.
    connection.execute("""CREATE TABLE IF NOT EXISTS auto_comment_targets (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, page_id TEXT NOT NULL,
        source_id TEXT NOT NULL, target_id TEXT, target_type TEXT,
        status TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}',
        checked_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(project_id, page_id, source_id))""")
    connection.execute("""CREATE INDEX IF NOT EXISTS ix_auto_comment_targets_page
        ON auto_comment_targets(project_id, page_id, status)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS auto_comment_submission_attempts (
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE,
        project_id TEXT NOT NULL, page_id TEXT NOT NULL, source_id TEXT NOT NULL,
        target_id TEXT NOT NULL, comment_text TEXT NOT NULL,
        remote_comment_id TEXT, status TEXT NOT NULL, error_category TEXT,
        error_message TEXT, verification_status TEXT NOT NULL DEFAULT 'NOT_ATTEMPTED',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    connection.execute("""CREATE INDEX IF NOT EXISTS ix_auto_comment_submission_history
        ON auto_comment_submission_attempts(project_id, page_id, source_id, created_at DESC)""")
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
        video_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(videos)")
        }
        if "facebook_uploaded" not in video_columns:
            connection.execute(
                """ALTER TABLE videos ADD COLUMN facebook_uploaded INTEGER
                   NOT NULL DEFAULT 0 CHECK(facebook_uploaded IN (0, 1))"""
            )
        for column, definition in (
            ("platform", "TEXT"),
            ("canonical_url", "TEXT"),
            ("external_video_id", "TEXT"),
            ("author_name", "TEXT"),
            ("duration_seconds", "REAL"),
            ("resolution", "TEXT"),
        ):
            if column not in video_columns:
                connection.execute(
                    f"ALTER TABLE videos ADD COLUMN {column} {definition}"
                )
        _create_global_video_url_triggers(connection)
        _create_download_jobs_table(connection)
        _create_matrices_tables(connection)
        _create_discovered_pages_table(connection)
        _create_reels_table(connection)
        _create_reel_metrics_history_table(connection)
        _create_collection_jobs_table(connection)
        _create_export_history_table(connection)
        _create_provider_configuration_table(connection)
        _create_facebook_publishing_tables(connection)
        _create_auto_comment_tables(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
