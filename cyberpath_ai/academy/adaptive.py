"""Deterministic skill evidence, review schedules and recommendations."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from cyberpath_ai.academy.content import Catalog, Lesson, Skill
from cyberpath_ai.academy.grading import Grade


Clock = Callable[[], datetime]


@dataclass(frozen=True)
class SkillStatus:
    skill: Skill
    assessed: int
    total: int
    percent: int | None


@dataclass(frozen=True)
class Recommendation:
    lesson: Lesson
    reason: str


class AdaptiveStore:
    """Additive tables: existing lesson, quiz and answer rows are never rewritten."""

    def __init__(self, path: Path, catalog: Catalog, *,
                 mastery_threshold_percent: int = 80,
                 clock: Clock | None = None,
                 success_intervals: tuple[int, ...] = (3, 7, 14, 30),
                 failure_interval: int = 1, session_size: int = 5,
                 timezone_for_user: Callable[[int], str] | None = None) -> None:
        if not success_intervals or any(value < 1 for value in success_intervals):
            raise ValueError("Review success intervals must be positive")
        if failure_interval < 1 or not 1 <= session_size <= 10:
            raise ValueError("Invalid review interval or session size")
        self.path = path
        self.catalog = catalog
        self.threshold = mastery_threshold_percent
        self.clock = clock or (lambda: datetime.now(UTC))
        self.success_intervals = success_intervals
        self.failure_interval = failure_interval
        self.session_size = session_size
        self.timezone_for_user = timezone_for_user or (lambda _user_id: "Asia/Almaty")
        self._initialize()
        self.reconcile_legacy_quizzes()

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Review clock must return a timezone-aware datetime")
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
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS skill_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, question_id TEXT NOT NULL, skill_id TEXT NOT NULL,
                source_key TEXT NOT NULL UNIQUE, source_kind TEXT NOT NULL,
                points REAL NOT NULL, hints_used INTEGER NOT NULL,
                revealed INTEGER NOT NULL, occurred_at_utc TEXT NOT NULL
            )""")
            connection.execute("""CREATE INDEX IF NOT EXISTS skill_evidence_user_question
                ON skill_evidence(user_id, question_id, occurred_at_utc, id)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS review_schedules (
                user_id INTEGER NOT NULL, question_id TEXT NOT NULL,
                last_review_at_utc TEXT NOT NULL, next_due_at_utc TEXT NOT NULL,
                interval_days INTEGER NOT NULL, success_streak INTEGER NOT NULL,
                latest_outcome TEXT NOT NULL,
                PRIMARY KEY(user_id, question_id)
            )""")
            connection.execute("""CREATE INDEX IF NOT EXISTS review_due_user
                ON review_schedules(user_id, next_due_at_utc)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS review_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                current_index INTEGER NOT NULL DEFAULT 0,
                started_at_utc TEXT NOT NULL, completed_at_utc TEXT
            )""")
            connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_active_review_per_user
                ON review_sessions(user_id) WHERE completed_at_utc IS NULL""")
            connection.execute("""CREATE TABLE IF NOT EXISTS review_items (
                session_id INTEGER NOT NULL, position INTEGER NOT NULL,
                question_id TEXT NOT NULL, lesson_id TEXT NOT NULL,
                PRIMARY KEY(session_id, position), UNIQUE(session_id, question_id)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS review_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
                position INTEGER NOT NULL, run_number INTEGER NOT NULL,
                hints_used INTEGER NOT NULL DEFAULT 0 CHECK(hints_used BETWEEN 0 AND 3),
                revealed INTEGER NOT NULL DEFAULT 0 CHECK(revealed IN (0, 1)),
                submitted_answer TEXT, points REAL, feedback TEXT,
                created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL,
                UNIQUE(session_id, position, run_number)
            )""")

    @staticmethod
    def effective_points(points: float, hints: int, revealed: bool) -> float:
        return 0.0 if revealed else max(0.0, min(1.0, points * (1 - 0.15 * hints)))

    def _record(self, connection: sqlite3.Connection, user_id: int, question_id: str,
                source_key: str, source_kind: str, points: float, hints: int,
                revealed: bool, occurred_at: datetime) -> bool:
        skill = self.catalog.skill_for_question.get(question_id)
        if skill is None:
            return False
        occurred_at = occurred_at.astimezone(UTC)
        timestamp = occurred_at.isoformat()
        inserted = connection.execute("""INSERT OR IGNORE INTO skill_evidence
            (user_id, question_id, skill_id, source_key, source_kind, points,
             hints_used, revealed, occurred_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, question_id, skill.id, source_key, source_kind,
             points, hints, int(revealed), timestamp)).rowcount
        if not inserted:
            return False
        previous = connection.execute("""SELECT * FROM review_schedules
            WHERE user_id = ? AND question_id = ?""", (user_id, question_id)).fetchone()
        if previous and datetime.fromisoformat(previous["last_review_at_utc"]) > occurred_at:
            return True  # Preserve a newer review schedule when importing legacy history.
        independent = points == 1.0 and hints == 0 and not revealed
        # Repeating a lesson quiz cannot accelerate the review ladder.
        streak = ((previous["success_streak"] + 1 if previous else 2)
                  if source_kind == "review" else 1) if independent else 0
        interval = (self.success_intervals[min(streak - 1, len(self.success_intervals) - 1)]
                    if independent else self.failure_interval)
        outcome = ("independent" if independent else "revealed" if revealed else
                   "hinted" if points == 1.0 and hints else "partial" if points > 0 else
                   "incorrect")
        next_due = (occurred_at + timedelta(days=interval)).isoformat()
        connection.execute("""INSERT INTO review_schedules
            (user_id, question_id, last_review_at_utc, next_due_at_utc,
             interval_days, success_streak, latest_outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, question_id) DO UPDATE SET
            last_review_at_utc=excluded.last_review_at_utc,
            next_due_at_utc=excluded.next_due_at_utc,
            interval_days=excluded.interval_days,
            success_streak=excluded.success_streak,
            latest_outcome=excluded.latest_outcome""",
            (user_id, question_id, timestamp, next_due, interval, streak, outcome))
        return True

    def _quiz_rows(self, connection: sqlite3.Connection, user_id: int | None = None):
        sql = """SELECT qa.id AS attempt_id, qa.user_id, qa.lesson_id, qr.*
            FROM quiz_attempts qa JOIN question_runs qr ON qr.attempt_id = qa.id
            WHERE qr.submitted_answer IS NOT NULL
            AND (qa.current_index > qr.question_index OR qa.completed_at_utc IS NOT NULL)
            AND qr.run_number = (SELECT MAX(inner_run.run_number) FROM question_runs inner_run
                WHERE inner_run.attempt_id = qr.attempt_id
                AND inner_run.question_index = qr.question_index
                AND inner_run.submitted_answer IS NOT NULL)"""
        args = ()
        if user_id is not None:
            sql += " AND qa.user_id = ?"
            args = (user_id,)
        sql += " ORDER BY qr.updated_at_utc, qr.id"
        return connection.execute(sql, args).fetchall()

    def _record_quiz_row(self, connection: sqlite3.Connection, row: sqlite3.Row) -> None:
        lesson = self.catalog.lesson(row["lesson_id"])
        index = row["question_index"]
        if lesson is None or not 0 <= index < len(lesson.questions):
            return
        self._record(connection, row["user_id"], lesson.questions[index]["id"],
                     f"quiz:{row['attempt_id']}:{index}", "quiz", row["points"],
                     row["hints_used"], bool(row["revealed"]),
                     datetime.fromisoformat(row["updated_at_utc"]))

    def reconcile_legacy_quizzes(self) -> None:
        """Idempotently import finalized Stage 4.1 question answers at startup."""
        with self._connect() as connection:
            for row in self._quiz_rows(connection):
                self._record_quiz_row(connection, row)

    def record_finalized_quiz_question(self, user_id: int, attempt_id: int,
                                       question_index: int) -> None:
        with self._connect() as connection:
            for row in self._quiz_rows(connection, user_id):
                if row["attempt_id"] == attempt_id and row["question_index"] == question_index:
                    self._record_quiz_row(connection, row)
                    return

    def latest_evidence(self, user_id: int) -> dict[str, sqlite3.Row]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT * FROM skill_evidence WHERE user_id = ?
                ORDER BY occurred_at_utc, id""", (user_id,)).fetchall()
        latest = {}
        for row in rows:
            latest[row["question_id"]] = row
        return latest

    def skill_statuses(self, user_id: int) -> list[SkillStatus]:
        latest = self.latest_evidence(user_id)
        statuses = []
        for skill in self.catalog.skills:
            evidence = [latest[question_id] for question_id in skill.question_ids
                        if question_id in latest]
            percent = (round(100 * sum(self.effective_points(row["points"], row["hints_used"],
                                                            bool(row["revealed"])) for row in evidence)
                             / len(evidence)) if evidence else None)
            statuses.append(SkillStatus(skill, len(evidence), len(skill.question_ids), percent))
        return statuses

    def due_questions(self, user_id: int, now: datetime | None = None) -> list[sqlite3.Row]:
        timestamp = (now or self.now()).astimezone(UTC).isoformat()
        with self._connect() as connection:
            rows = connection.execute("""SELECT * FROM review_schedules
                WHERE user_id = ? AND next_due_at_utc <= ?""", (user_id, timestamp)).fetchall()
        weakness = {status.skill.id: status.percent for status in self.skill_statuses(user_id)}
        eligible = [row for row in rows if row["question_id"] in self.catalog.question_by_id]
        return sorted(eligible, key=lambda row: (
            weakness.get(self.catalog.skill_for_question[row["question_id"]].id) or 0,
            row["next_due_at_utc"], row["question_id"]))

    def upcoming(self, user_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM review_schedules
                WHERE user_id = ? ORDER BY next_due_at_utc LIMIT 1""", (user_id,)).fetchone()

    def schedules(self, user_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM review_schedules
                WHERE user_id = ? ORDER BY next_due_at_utc, question_id""", (user_id,)).fetchall()

    def local_due(self, user_id: int, timestamp: str) -> str:
        timezone = self.timezone_for_user(user_id)
        local = datetime.fromisoformat(timestamp).astimezone(ZoneInfo(timezone))
        return local.strftime("%d.%m.%Y %H:%M") + f" ({timezone})"

    def recommend(self, user_id: int) -> Recommendation | None:
        latest = self.latest_evidence(user_id)
        statuses = self.skill_statuses(user_id)
        weak = sorted((status for status in statuses if status.percent is not None
                       and status.percent < self.threshold), key=lambda status: status.percent)
        due = self.due_questions(user_id)
        if due:
            question_id = due[0]["question_id"]
            lesson = self.catalog.question_by_id[question_id][0]
            count = sum(self.catalog.question_by_id[row["question_id"]][0].id == lesson.id
                        for row in due)
            return self._with_prerequisite(user_id, lesson,
                f"Есть {count} вопрос(ов) этого урока, срок повторения которых наступил. ")
        cutoff = self.now() - timedelta(days=7)
        for module in self.catalog.modules:
            for lesson in module.lessons:
                mistakes = sum(
                    row["question_id"] in {question["id"] for question in lesson.questions}
                    and datetime.fromisoformat(row["occurred_at_utc"]) >= cutoff
                    and self.effective_points(row["points"], row["hints_used"],
                                              bool(row["revealed"])) < 0.8
                    for row in latest.values())
                if mistakes >= 2:
                    return self._with_prerequisite(user_id, lesson,
                        f"По {mistakes} вопросам урока недавно были ошибки или подсказки. ")
        if weak:
            chosen = weak[0]
            lesson = self.catalog.lesson(chosen.skill.lesson_id)
            return self._with_prerequisite(user_id, lesson,
                f"Наблюдаемый результат по теме «{chosen.skill.title}» — "
                f"{chosen.percent}% на {chosen.assessed} проверенных вопрос(ах). ")
        for module in self.catalog.modules:
            for lesson in module.lessons:
                with self._connect() as connection:
                    row = connection.execute("""SELECT completed_at_utc FROM lesson_progress
                        WHERE user_id = ? AND lesson_id = ?""", (user_id, lesson.id)).fetchone()
                if row is None or row["completed_at_utc"] is None:
                    return self._with_prerequisite(user_id, lesson,
                        "Этот урок ещё не отмечен как прочитанный. ")
        return None

    def _with_prerequisite(self, user_id: int, lesson: Lesson, reason: str) -> Recommendation:
        module = self.catalog.module_by_id[lesson.module_id]
        for prerequisite in module.lessons:
            if prerequisite.id == lesson.id:
                break
            with self._connect() as connection:
                row = connection.execute("""SELECT completed_at_utc FROM lesson_progress
                    WHERE user_id = ? AND lesson_id = ?""",
                    (user_id, prerequisite.id)).fetchone()
            if row is None or row["completed_at_utc"] is None:
                return Recommendation(prerequisite,
                    reason + f"Перед «{lesson.title}» полезно пройти «{prerequisite.title}». "
                    "Это рекомендация; другие уроки остаются доступны.")
        return Recommendation(lesson, reason.strip())

    def active_review_session(self, user_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM review_sessions
                WHERE user_id = ? AND completed_at_utc IS NULL ORDER BY id DESC LIMIT 1""",
                (user_id,)).fetchone()

    def review_session(self, user_id: int, session_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM review_sessions
                WHERE id = ? AND user_id = ?""", (session_id, user_id)).fetchone()

    def review_items(self, user_id: int, session_id: int) -> list[sqlite3.Row]:
        if self.review_session(user_id, session_id) is None:
            return []
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM review_items
                WHERE session_id = ? ORDER BY position""", (session_id,)).fetchall()

    def start_review_session(self, user_id: int) -> int | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute("""SELECT id FROM review_sessions
                WHERE user_id = ? AND completed_at_utc IS NULL""", (user_id,)).fetchone()
            if active:
                return active["id"]
            due = self.due_questions(user_id)[:self.session_size]
            if not due:
                return None
            now = self.now().isoformat()
            cursor = connection.execute("""INSERT INTO review_sessions
                (user_id, started_at_utc) VALUES (?, ?)""", (user_id, now))
            session_id = cursor.lastrowid
            for position, schedule in enumerate(due):
                lesson = self.catalog.question_by_id[schedule["question_id"]][0]
                connection.execute("""INSERT INTO review_items
                    (session_id, position, question_id, lesson_id) VALUES (?, ?, ?, ?)""",
                    (session_id, position, schedule["question_id"], lesson.id))
            return session_id

    def review_run(self, user_id: int, session_id: int, position: int) -> sqlite3.Row | None:
        if self.review_session(user_id, session_id) is None:
            return None
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM review_runs
                WHERE session_id = ? AND position = ? ORDER BY run_number DESC LIMIT 1""",
                (session_id, position)).fetchone()

    def ensure_review_run(self, user_id: int, session_id: int, position: int) -> sqlite3.Row:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = connection.execute("""SELECT * FROM review_sessions
                WHERE id = ? AND user_id = ?""", (session_id, user_id)).fetchone()
            if session is None or session["completed_at_utc"] or session["current_index"] != position:
                raise ValueError("Outdated review question")
            row = connection.execute("""SELECT * FROM review_runs WHERE session_id = ?
                AND position = ? ORDER BY run_number DESC LIMIT 1""",
                (session_id, position)).fetchone()
            if row is None:
                now = self.now().isoformat()
                cursor = connection.execute("""INSERT INTO review_runs
                    (session_id, position, run_number, created_at_utc, updated_at_utc)
                    VALUES (?, ?, 1, ?, ?)""", (session_id, position, now, now))
                row = connection.execute("SELECT * FROM review_runs WHERE id = ?",
                                         (cursor.lastrowid,)).fetchone()
            return row

    def _review_update(self, user_id: int, session_id: int, run_id: int,
                       sql: str, args: tuple) -> sqlite3.Row | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = connection.execute("""SELECT * FROM review_sessions
                WHERE id = ? AND user_id = ? AND completed_at_utc IS NULL""",
                (session_id, user_id)).fetchone()
            if session is None:
                return None
            changed = connection.execute(sql, (*args, run_id, session_id,
                                               session["current_index"])).rowcount
            if not changed:
                return None
            return connection.execute("SELECT * FROM review_runs WHERE id = ?", (run_id,)).fetchone()

    def review_hint(self, user_id: int, session_id: int, run_id: int) -> sqlite3.Row | None:
        return self._review_update(user_id, session_id, run_id, """UPDATE review_runs
            SET hints_used = hints_used + 1, updated_at_utc = ?
            WHERE id = ? AND session_id = ? AND position = ?
            AND submitted_answer IS NULL AND hints_used < 3""", (self.now().isoformat(),))

    def review_submit(self, user_id: int, session_id: int, run_id: int,
                      answer: str, result: Grade) -> bool:
        return self._review_update(user_id, session_id, run_id, """UPDATE review_runs
            SET submitted_answer = ?, points = ?, feedback = ?, updated_at_utc = ?
            WHERE id = ? AND session_id = ? AND position = ?
            AND submitted_answer IS NULL AND revealed = 0""",
            (answer, result.points, result.feedback, self.now().isoformat())) is not None

    def review_reveal(self, user_id: int, session_id: int, run_id: int) -> bool:
        return self._review_update(user_id, session_id, run_id, """UPDATE review_runs
            SET revealed = 1, submitted_answer = '[shown answer]', points = 0,
            feedback = 'Ответ показан; это не успешное повторение.', updated_at_utc = ?
            WHERE id = ? AND session_id = ? AND position = ?
            AND submitted_answer IS NULL AND hints_used = 3""",
            (self.now().isoformat(),)) is not None

    def review_retry(self, user_id: int, session_id: int, run_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = connection.execute("""SELECT * FROM review_sessions
                WHERE id = ? AND user_id = ? AND completed_at_utc IS NULL""",
                (session_id, user_id)).fetchone()
            if session is None:
                return None
            position = session["current_index"]
            run = connection.execute("""SELECT * FROM review_runs
                WHERE session_id = ? AND position = ? ORDER BY run_number DESC LIMIT 1""",
                (session_id, position)).fetchone()
            if run is None or run["id"] != run_id or run["submitted_answer"] is None:
                return None
            now = self.now().isoformat()
            cursor = connection.execute("""INSERT INTO review_runs
                (session_id, position, run_number, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?)""", (session_id, position, run["run_number"] + 1,
                                               now, now))
            return connection.execute("SELECT * FROM review_runs WHERE id = ?",
                                      (cursor.lastrowid,)).fetchone()

    def review_advance(self, user_id: int, session_id: int, run_id: int) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = connection.execute("""SELECT * FROM review_sessions
                WHERE id = ? AND user_id = ? AND completed_at_utc IS NULL""",
                (session_id, user_id)).fetchone()
            if session is None:
                return False
            position = session["current_index"]
            run = connection.execute("""SELECT * FROM review_runs WHERE session_id = ?
                AND position = ? ORDER BY run_number DESC LIMIT 1""",
                (session_id, position)).fetchone()
            if run is None or run["id"] != run_id or run["submitted_answer"] is None:
                return False
            item = connection.execute("""SELECT * FROM review_items WHERE session_id = ?
                AND position = ?""", (session_id, position)).fetchone()
            self._record(connection, user_id, item["question_id"],
                         f"review:{session_id}:{position}", "review", run["points"],
                         run["hints_used"], bool(run["revealed"]),
                         datetime.fromisoformat(run["updated_at_utc"]))
            remaining = connection.execute("""SELECT 1 FROM review_items
                WHERE session_id = ? AND position = ?""", (session_id, position + 1)).fetchone()
            if remaining:
                connection.execute("""UPDATE review_sessions SET current_index = ?
                    WHERE id = ?""", (position + 1, session_id))
            else:
                connection.execute("""UPDATE review_sessions SET current_index = ?,
                    completed_at_utc = ? WHERE id = ?""",
                    (position + 1, self.now().isoformat(), session_id))
            return True

    def review_results(self, user_id: int, session_id: int) -> list[sqlite3.Row]:
        if self.review_session(user_id, session_id) is None:
            return []
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM review_runs
                WHERE session_id = ? AND submitted_answer IS NOT NULL
                AND run_number = (SELECT MAX(inner_run.run_number) FROM review_runs inner_run
                    WHERE inner_run.session_id = review_runs.session_id
                    AND inner_run.position = review_runs.position)
                ORDER BY position""", (session_id,)).fetchall()
