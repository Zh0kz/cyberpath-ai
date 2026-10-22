"""Daily, source-backed learning missions and idempotent activity evidence."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from cyberpath_ai.academy.adaptive import AdaptiveStore
from cyberpath_ai.academy.content import Catalog
from cyberpath_ai.news.models import NewsItem


@dataclass(frozen=True)
class Mission:
    id: int
    user_id: int
    local_date: date
    timezone: str
    lesson_id: str
    reason: str
    news_ids: tuple[str, ...]
    quiz_ids: tuple[str, ...]
    review_ids: tuple[str, ...]
    viewed_news_ids: tuple[str, ...]
    lesson_done: bool
    quiz_done: bool
    review_done: bool
    completed_at: str | None

    @property
    def available(self) -> int:
        return 2 + bool(self.news_ids) + bool(self.review_ids)

    @property
    def done(self) -> int:
        return (int(self.lesson_done) + int(self.quiz_done) +
                int(bool(self.news_ids) and len(self.viewed_news_ids) == len(self.news_ids)) +
                int(bool(self.review_ids) and self.review_done))


class MissionStore:
    def __init__(self, path: Path, catalog: Catalog, adaptive: AdaptiveStore,
                 timezone_for_user: Callable[[int], str],
                 clock: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self.catalog = catalog
        self.adaptive = adaptive
        self.timezone_for_user = timezone_for_user
        self.clock = clock or (lambda: datetime.now(UTC))
        self._initialize()

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Mission clock must be timezone-aware")
        return value.astimezone(UTC)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS daily_missions (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                local_date TEXT NOT NULL, timezone TEXT NOT NULL,
                lesson_id TEXT NOT NULL, reason TEXT NOT NULL,
                news_ids_json TEXT NOT NULL, quiz_ids_json TEXT NOT NULL,
                review_ids_json TEXT NOT NULL, created_at_utc TEXT NOT NULL,
                lesson_completed_at_utc TEXT, quiz_completed_at_utc TEXT,
                review_completed_at_utc TEXT, completed_at_utc TEXT,
                UNIQUE(user_id, local_date)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS mission_news_views (
                mission_id INTEGER NOT NULL, article_id TEXT NOT NULL,
                viewed_at_utc TEXT NOT NULL,
                PRIMARY KEY(mission_id, article_id)
            )""")

    def local_date(self, user_id: int, now: datetime | None = None) -> date:
        zone = ZoneInfo(self.timezone_for_user(user_id))
        return (now or self.now()).astimezone(zone).date()

    def _row(self, db: sqlite3.Connection, user_id: int, day: date) -> sqlite3.Row | None:
        return db.execute("SELECT * FROM daily_missions WHERE user_id = ? AND local_date = ?",
                          (user_id, day.isoformat())).fetchone()

    def get(self, user_id: int, day: date | None = None) -> Mission | None:
        with self._connect() as db:
            row = self._row(db, user_id, day or self.local_date(user_id))
            if row is None:
                return None
            viewed = db.execute("""SELECT article_id FROM mission_news_views
                WHERE mission_id = ? ORDER BY article_id""", (row["id"],)).fetchall()
        return Mission(row["id"], row["user_id"], date.fromisoformat(row["local_date"]),
                       row["timezone"], row["lesson_id"], row["reason"],
                       tuple(json.loads(row["news_ids_json"])),
                       tuple(json.loads(row["quiz_ids_json"])),
                       tuple(json.loads(row["review_ids_json"])),
                       tuple(item["article_id"] for item in viewed),
                       bool(row["lesson_completed_at_utc"]),
                       bool(row["quiz_completed_at_utc"]),
                       bool(row["review_completed_at_utc"]), row["completed_at_utc"])

    def create_today(self, user_id: int, articles: tuple[NewsItem, ...] = ()) -> Mission:
        now = self.now()
        day = self.local_date(user_id, now)
        existing = self.get(user_id, day)
        if existing:
            return self.refresh(user_id, day)
        recommendation = self.adaptive.recommend(user_id)
        if recommendation is None:
            lesson = self.catalog.modules[0].lessons[0]
            reason = "Все уроки прочитаны; можно повторить этот урок и проверить знания."
        else:
            lesson, reason = recommendation.lesson, recommendation.reason
        due = self.adaptive.due_questions(user_id, now)
        review_ids = tuple(row["question_id"] for row in due[:self.adaptive.session_size])
        measured_weakness = any(status.percent is not None
                                and status.percent < self.adaptive.threshold
                                for status in self.adaptive.skill_statuses(user_id))
        if not due and not measured_weakness:
            with self._connect() as db:
                active = db.execute("""SELECT lesson_id FROM quiz_attempts
                    WHERE user_id = ? AND completed_at_utc IS NULL
                    ORDER BY id DESC LIMIT 1""", (user_id,)).fetchone()
                reading = db.execute("""SELECT lesson_id, last_section FROM lesson_progress
                    WHERE user_id = ? ORDER BY last_studied_at_utc DESC LIMIT 1""",
                    (user_id,)).fetchone()
            if active and (unfinished := self.catalog.lesson(active["lesson_id"])):
                lesson = unfinished
                reason = "У вас есть незавершённый квиз этого урока; продолжите с сохранённого вопроса."
            elif reading and (unfinished := self.catalog.lesson(reading["lesson_id"])) \
                    and reading["last_section"] < len(unfinished.sections) + 1:
                lesson = unfinished
                reason = "Вы уже начали этот урок; продолжите с сохранённого раздела."
        news_ids = tuple(dict.fromkeys(item.identity_key for item in articles))[:3]
        with self._connect() as db:
            db.execute("""INSERT OR IGNORE INTO daily_missions
                (user_id, local_date, timezone, lesson_id, reason, news_ids_json,
                 quiz_ids_json, review_ids_json, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, day.isoformat(), self.timezone_for_user(user_id), lesson.id,
                 reason, json.dumps(news_ids),
                 json.dumps([q["id"] for q in lesson.questions]),
                 json.dumps(review_ids), now.isoformat()))
        return self.refresh(user_id, day)

    def _bounds(self, mission: Mission) -> tuple[str, str]:
        zone = ZoneInfo(mission.timezone)
        start = datetime.combine(mission.local_date, time.min, zone).astimezone(UTC)
        end = datetime.combine(mission.local_date + timedelta(days=1), time.min,
                               zone).astimezone(UTC)
        return start.isoformat(), end.isoformat()

    def refresh(self, user_id: int, day: date | None = None) -> Mission | None:
        mission = self.get(user_id, day)
        if mission is None:
            return None
        start, end = self._bounds(mission)
        now = self.now().isoformat()
        with self._connect() as db:
            # A finished quiz counts even when some answers were wrong or revealed.
            quiz = db.execute("""SELECT 1 FROM quiz_attempts WHERE user_id = ?
                AND lesson_id = ? AND completed_at_utc >= ? AND completed_at_utc < ?
                LIMIT 1""", (user_id, mission.lesson_id, start, end)).fetchone()
            reviewed = all(db.execute("""SELECT 1 FROM skill_evidence WHERE user_id = ?
                AND question_id = ? AND source_kind = 'review'
                AND occurred_at_utc >= ? AND occurred_at_utc < ? LIMIT 1""",
                (user_id, question_id, start, end)).fetchone()
                for question_id in mission.review_ids)
            if quiz:
                db.execute("""UPDATE daily_missions SET quiz_completed_at_utc =
                    COALESCE(quiz_completed_at_utc, ?) WHERE id = ? AND user_id = ?""",
                    (now, mission.id, user_id))
            if mission.review_ids and reviewed:
                db.execute("""UPDATE daily_missions SET review_completed_at_utc =
                    COALESCE(review_completed_at_utc, ?) WHERE id = ? AND user_id = ?""",
                    (now, mission.id, user_id))
            row = db.execute("SELECT * FROM daily_missions WHERE id = ?", (mission.id,)).fetchone()
            viewed = db.execute("SELECT COUNT(*) FROM mission_news_views WHERE mission_id = ?",
                                (mission.id,)).fetchone()[0]
            if (row["lesson_completed_at_utc"] and row["quiz_completed_at_utc"]
                    and (not mission.news_ids or viewed >= len(mission.news_ids))
                    and (not mission.review_ids or row["review_completed_at_utc"])):
                db.execute("""UPDATE daily_missions SET completed_at_utc =
                    COALESCE(completed_at_utc, ?) WHERE id = ? AND user_id = ?""",
                    (now, mission.id, user_id))
        return self.get(user_id, mission.local_date)

    def mark_lesson_summary(self, user_id: int, lesson_id: str) -> Mission | None:
        mission = self.get(user_id)
        if mission is None or mission.lesson_id != lesson_id:
            return mission
        now = self.now().isoformat()
        with self._connect() as db:
            record = db.execute("""SELECT last_section FROM lesson_progress
                WHERE user_id = ? AND lesson_id = ?""", (user_id, lesson_id)).fetchone()
            lesson = self.catalog.lesson(lesson_id)
            if record and lesson and record["last_section"] == len(lesson.sections) + 1:
                db.execute("""UPDATE daily_missions SET lesson_completed_at_utc =
                    COALESCE(lesson_completed_at_utc, ?) WHERE id = ? AND user_id = ?""",
                    (now, mission.id, user_id))
        return self.refresh(user_id)

    def mark_news_viewed(self, user_id: int, article_id: str) -> Mission | None:
        mission = self.get(user_id)
        if mission is None or article_id not in mission.news_ids:
            return mission
        with self._connect() as db:
            db.execute("""INSERT OR IGNORE INTO mission_news_views
                (mission_id, article_id, viewed_at_utc) VALUES (?, ?, ?)""",
                (mission.id, article_id, self.now().isoformat()))
        return self.refresh(user_id)
