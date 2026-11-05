from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Mapping

from app.database.connection import Database
from app.services.viral_reels_service import PageCandidate


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ViralReelsStore:
    def __init__(self, database: Database) -> None:
        self.connection = database.connection
        self._database_path = database.path
        self.repair_legacy_pages()

    @classmethod
    def for_thread(cls, database_path: str) -> "ViralReelsStore":
        return cls(Database(database_path))

    def close(self) -> None:
        self.connection.close()

    def repair_legacy_pages(self) -> int:
        repaired = 0
        try:
            rows = self.connection.execute("SELECT page_id, page_name, page_url FROM discovered_pages").fetchall()
        except sqlite3.OperationalError:
            return 0
        from urllib.parse import parse_qs, urlsplit
        for row in rows:
            old_id = str(row["page_id"])
            old_name = str(row["page_name"])
            old_url = str(row["page_url"])

            recovered_id = None
            for candidate in (old_url, old_id):
                if "profile.php" in candidate and "id=" in candidate:
                    try:
                        p_parts = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
                        qs = parse_qs(p_parts.query)
                        cand_id = qs.get("id", [""])[0]
                        if cand_id and cand_id.isdigit():
                            recovered_id = cand_id
                            break
                    except Exception:
                        pass

            if not recovered_id and "profile.php" in old_url:
                try:
                    reel_row = self.connection.execute(
                        "SELECT page_id FROM reels WHERE page_url=? AND page_id NOT LIKE 'manual:%' AND page_id != ''",
                        (old_url,),
                    ).fetchone()
                    if reel_row and str(reel_row["page_id"]).isdigit():
                        recovered_id = str(reel_row["page_id"])
                except Exception:
                    pass

            if recovered_id:
                new_url = f"https://www.facebook.com/profile.php?id={recovered_id}"
                new_name = old_name if old_name and old_name != "profile.php" else f"Page {recovered_id}"
                with self.connection:
                    self.connection.execute(
                        "UPDATE discovered_pages SET page_id=?, page_name=?, page_url=? WHERE page_id=?",
                        (recovered_id, new_name, new_url, old_id),
                    )
                repaired += 1
        return repaired

    def upsert_page(self, page: PageCandidate) -> bool:
        with self.connection:
            existing = None
            if page.page_id:
                existing = self.connection.execute(
                    "SELECT page_id, page_url FROM discovered_pages WHERE page_id=?",
                    (page.page_id,),
                ).fetchone()
            if not existing and page.page_url:
                existing = self.connection.execute(
                    "SELECT page_id, page_url FROM discovered_pages WHERE page_url=?",
                    (page.page_url,),
                ).fetchone()
            if existing:
                self.connection.execute(
                    """UPDATE discovered_pages SET
                         page_id=?, page_name=?, page_url=?, country=?,
                         language=?, topic=?, source=?, is_saved=1
                       WHERE page_id=?""",
                    (page.page_id or existing["page_id"], page.page_name, page.page_url, page.country,
                     page.language, page.topic, page.source,
                     existing["page_id"]),
                )
                return True
            cursor = self.connection.execute(
                """INSERT INTO discovered_pages
                   (page_id, page_name, page_url, country, language, topic, source, discovered_at, is_saved)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (page.page_id, page.page_name, page.page_url, page.country, page.language,
                 page.topic, page.source, _now()),
            )
        return cursor.rowcount > 0

    def delete_pages(self, page_urls: list[str]) -> int:
        if not page_urls:
            return 0
        with self.connection:
            page_rows = self.connection.execute(
                "SELECT page_id FROM discovered_pages WHERE page_url IN ({})".format(
                    ",".join("?" for _ in page_urls)
                ),
                tuple(page_urls),
            ).fetchall()
            page_ids = [str(row["page_id"]) for row in page_rows]
            if page_ids:
                self.connection.execute(
                    "DELETE FROM reels WHERE page_id IN ({}) OR page_url IN ({})".format(
                        ",".join("?" for _ in page_ids), ",".join("?" for _ in page_urls)
                    ),
                    tuple(page_ids) + tuple(page_urls),
                )
            cursor = self.connection.executemany(
                "DELETE FROM discovered_pages WHERE page_url=?",
                ((url,) for url in page_urls),
            )
        return cursor.rowcount

    def list_pages(self, country: str = "All", language: str = "All", topic: str = "", limit: int = 50) -> list[sqlite3.Row]:
        clauses, params = [], []
        for column, value, ignored in (("country", country, "All"), ("language", language, "All"), ("topic", topic, "")):
            if value != ignored:
                clauses.append(f"{column}=?")
                params.append(value)
        clauses.insert(0, "is_saved=1")
        where = f" WHERE {' AND '.join(clauses)}"
        return list(self.connection.execute(f"SELECT * FROM discovered_pages{where} ORDER BY discovered_at DESC LIMIT ?", (*params, limit)))

    def upsert_reel(self, reel: Mapping[str, object]) -> bool:
        exists = self.connection.execute("SELECT 1 FROM reels WHERE reel_id=?", (reel["reel_id"],)).fetchone() is not None
        now = _now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO reels
                   (reel_id, reel_url, page_id, page_name, page_url, caption, views,
                    likes, comments, shares, published_at, collected_at, country,
                    language, topic, viral_score, data_source, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(reel_id) DO UPDATE SET
                    reel_url=excluded.reel_url, page_name=excluded.page_name,
                    caption=excluded.caption, views=excluded.views, likes=excluded.likes,
                    comments=excluded.comments, shares=excluded.shares,
                    published_at=excluded.published_at, country=excluded.country,
                    language=excluded.language, topic=excluded.topic,
                    data_source=excluded.data_source, status=excluded.status,
                    updated_at=excluded.updated_at""",
                (reel["reel_id"], reel["reel_url"], reel["page_id"], reel["page_name"],
                 reel["page_url"], reel.get("caption", ""), reel.get("views"),
                 reel.get("likes"), reel.get("comments"), reel.get("shares"),
                 reel.get("published_at"), now, reel.get("country"), reel.get("language"),
                 reel.get("topic") or "", reel.get("viral_score", 0), reel.get("data_source") or "",
                 reel.get("status") or "collected", now),
            )
        return not exists

    def record_metrics_snapshot(self, reel_id: str, views: int | None, likes: int | None, comments: int | None, shares: int | None) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO reel_metrics_history(reel_id, views, likes, comments, shares, measured_at) VALUES (?, ?, ?, ?, ?, ?)",
                (reel_id, views, likes, comments, shares, _now()),
            )

    def metrics_history(self, reel_id: str) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM reel_metrics_history WHERE reel_id=? ORDER BY measured_at, id", (reel_id,))]

    def update_viral_score(self, reel_id: str, score: float) -> None:
        with self.connection:
            self.connection.execute("UPDATE reels SET viral_score=?, updated_at=? WHERE reel_id=?", (score, _now(), reel_id))

    def list_reels(self, limit: int = 1000) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM reels ORDER BY viral_score DESC, views DESC LIMIT ?", (limit,)))

    def start_job(self, project_id: str | None = None) -> str:
        running = self.connection.execute("SELECT job_id FROM collection_jobs WHERE status='running'").fetchone()
        if running:
            raise RuntimeError("A collection job is already running")
        job_id = uuid.uuid4().hex
        with self.connection:
            self.connection.execute("INSERT INTO collection_jobs(job_id, project_id, started_at, status) VALUES (?, ?, ?, 'running')", (job_id, project_id, _now()))
        return job_id

    def finish_job(self, job_id: str, status: str, pages: int, reels: int, qualified: int, errors: int) -> None:
        with self.connection:
            self.connection.execute(
                """UPDATE collection_jobs SET completed_at=?, status=?, pages_discovered=?,
                   reels_collected=?, reels_qualified=?, errors=? WHERE job_id=?""",
                (_now(), status, pages, reels, qualified, errors, job_id),
            )

    def set_provider_configuration(self, name: str, configuration: Mapping[str, object], active: bool = True) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO provider_configuration(provider_name, config_json, last_configured_at, is_active)
                   VALUES (?, ?, ?, ?) ON CONFLICT(provider_name) DO UPDATE SET
                   config_json=excluded.config_json, last_configured_at=excluded.last_configured_at,
                   is_active=excluded.is_active""",
                (name, json.dumps(configuration), _now(), int(active)),
            )

    def provider_configuration(self, name: str) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT config_json FROM provider_configuration WHERE provider_name=? AND is_active=1",
            (name,),
        ).fetchone()
        return json.loads(row["config_json"]) if row else {}

    def set_schedule(self, schedule: str) -> None:
        if schedule not in {"manual", "6h", "12h", "daily"}:
            raise ValueError("Unsupported schedule")
        self.set_provider_configuration("scheduler", {"schedule": schedule}, schedule != "manual")

    def schedule(self) -> str:
        config = self.provider_configuration("scheduler")
        return str(config.get("schedule", "manual"))

    def last_success_at(self) -> datetime | None:
        row = self.connection.execute(
            "SELECT completed_at FROM collection_jobs WHERE status='completed' ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        return datetime.fromisoformat(row["completed_at"]) if row and row["completed_at"] else None

    def job_running(self) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM collection_jobs WHERE status='running' LIMIT 1"
        ).fetchone() is not None
