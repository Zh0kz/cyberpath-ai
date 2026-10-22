"""SQLite cache for source records and fetch state."""

from datetime import UTC, datetime, timedelta
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from collections.abc import Iterator

from cyberpath_ai.news.models import NewsItem, sort_news


class NewsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute(
            """CREATE TABLE IF NOT EXISTS articles (
                identity_key TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                published_at_utc TEXT NOT NULL,
                date_precision TEXT NOT NULL,
                source_url TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                cve_ids_json TEXT NOT NULL,
                severity_rank INTEGER NOT NULL,
                fetched_at_utc TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS articles_published_idx ON articles(published_at_utc)"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS fetch_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_attempt_utc TEXT,
                last_success_utc TEXT,
                failed_sources_json TEXT NOT NULL DEFAULT '[]'
            )"""
        )
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, items: list[NewsItem], fetched_at: datetime) -> None:
        with self._connect() as connection:
            connection.executemany(
                """INSERT INTO articles (
                    identity_key, source, title, description, published_at_utc,
                    date_precision, source_url, canonical_url, cve_ids_json,
                    severity_rank, fetched_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_key) DO UPDATE SET
                    source = excluded.source,
                    title = excluded.title,
                    description = excluded.description,
                    published_at_utc = excluded.published_at_utc,
                    date_precision = excluded.date_precision,
                    source_url = excluded.source_url,
                    canonical_url = excluded.canonical_url,
                    cve_ids_json = excluded.cve_ids_json,
                    severity_rank = excluded.severity_rank,
                    fetched_at_utc = excluded.fetched_at_utc""",
                [
                    (
                        item.identity_key,
                        item.source,
                        item.title,
                        item.description,
                        item.published_at.isoformat(),
                        item.date_precision,
                        item.source_url,
                        item.canonical_url,
                        json.dumps(item.cve_ids),
                        item.severity_rank,
                        fetched_at.astimezone(UTC).isoformat(),
                    )
                    for item in items
                ],
            )

    def recent(self, now: datetime, lookback: timedelta) -> list[NewsItem]:
        # Include an extra day for date-only KEV records, then apply the precise
        # overlap rule in Python.
        earliest = (now.astimezone(UTC) - lookback - timedelta(days=1)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM articles WHERE published_at_utc >= ?", (earliest,)
            ).fetchall()
        items = [
            NewsItem(
                source=row["source"],
                title=row["title"],
                description=row["description"],
                published_at=datetime.fromisoformat(row["published_at_utc"]),
                date_precision=row["date_precision"],
                source_url=row["source_url"],
                cve_ids=tuple(json.loads(row["cve_ids_json"])),
                severity_rank=row["severity_rank"],
            )
            for row in rows
        ]
        return sort_news([item for item in items if item.is_recent(now, lookback)])

    def count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    def get(self, identity_key: str) -> NewsItem | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM articles WHERE identity_key = ?",
                                     (identity_key,)).fetchone()
        if row is None:
            return None
        return NewsItem(
            source=row["source"], title=row["title"], description=row["description"],
            published_at=datetime.fromisoformat(row["published_at_utc"]),
            date_precision=row["date_precision"], source_url=row["source_url"],
            cve_ids=tuple(json.loads(row["cve_ids_json"])),
            severity_rank=row["severity_rank"],
        )

    def fetch_state(self) -> tuple[datetime | None, datetime | None, tuple[str, ...]]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM fetch_state WHERE id = 1").fetchone()
        if row is None:
            return None, None, ()
        return (
            datetime.fromisoformat(row["last_attempt_utc"]) if row["last_attempt_utc"] else None,
            datetime.fromisoformat(row["last_success_utc"]) if row["last_success_utc"] else None,
            tuple(json.loads(row["failed_sources_json"])),
        )

    def record_fetch(self, attempted_at: datetime, failed_sources: tuple[str, ...]) -> None:
        with self._connect() as connection:
            prior = connection.execute(
                "SELECT last_success_utc FROM fetch_state WHERE id = 1"
            ).fetchone()
            success = attempted_at.astimezone(UTC).isoformat() if not failed_sources else (
                prior[0] if prior else None
            )
            connection.execute(
                """INSERT INTO fetch_state (
                    id, last_attempt_utc, last_success_utc, failed_sources_json
                ) VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_attempt_utc = excluded.last_attempt_utc,
                    last_success_utc = excluded.last_success_utc,
                    failed_sources_json = excluded.failed_sources_json""",
                (attempted_at.astimezone(UTC).isoformat(), success, json.dumps(failed_sources)),
            )
