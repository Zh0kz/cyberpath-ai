"""SQLite reading progress and immutable quiz response history."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from cyberpath_ai.academy.content import Catalog, Lesson
from cyberpath_ai.academy.grading import Grade


class AcademyStore:
    def __init__(self, path: Path, catalog: Catalog, mastery_threshold_percent: int = 80,
                 *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self.catalog = catalog
        self.mastery_threshold_percent = mastery_threshold_percent
        self.clock = clock or (lambda: datetime.now(UTC))
        self._initialize()

    def _now(self) -> str:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Academy clock must be timezone-aware")
        return value.astimezone(UTC).isoformat()

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
            connection.execute("""CREATE TABLE IF NOT EXISTS lesson_progress (
                user_id INTEGER NOT NULL, lesson_id TEXT NOT NULL,
                last_section INTEGER NOT NULL, last_studied_at_utc TEXT NOT NULL,
                completed_at_utc TEXT, mastered_at_utc TEXT,
                PRIMARY KEY(user_id, lesson_id)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS quiz_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                lesson_id TEXT NOT NULL, current_index INTEGER NOT NULL DEFAULT 0,
                started_at_utc TEXT NOT NULL, completed_at_utc TEXT,
                total_points REAL
            )""")
            connection.execute("""CREATE INDEX IF NOT EXISTS quiz_user_lesson_idx
                ON quiz_attempts(user_id, lesson_id, id)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS question_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id INTEGER NOT NULL,
                question_index INTEGER NOT NULL, run_number INTEGER NOT NULL,
                hints_used INTEGER NOT NULL DEFAULT 0 CHECK(hints_used BETWEEN 0 AND 3),
                revealed INTEGER NOT NULL DEFAULT 0 CHECK(revealed IN (0, 1)),
                submitted_answer TEXT, points REAL, feedback TEXT,
                created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL,
                UNIQUE(attempt_id, question_index, run_number),
                FOREIGN KEY(attempt_id) REFERENCES quiz_attempts(id)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS module_mastery (
                user_id INTEGER NOT NULL, module_id TEXT NOT NULL,
                mastered_lessons INTEGER NOT NULL, total_lessons INTEGER NOT NULL,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY(user_id, module_id)
            )""")

    def view_section(self, user_id: int, lesson: Lesson, section: int) -> None:
        if not 0 <= section <= len(lesson.sections) + 1:
            raise ValueError("Invalid lesson section")
        now = self._now()
        completed = now if section == len(lesson.sections) + 1 else None
        with self._connect() as connection:
            connection.execute("""INSERT INTO lesson_progress
                (user_id, lesson_id, last_section, last_studied_at_utc, completed_at_utc)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, lesson_id) DO UPDATE SET
                last_section = excluded.last_section,
                last_studied_at_utc = excluded.last_studied_at_utc,
                completed_at_utc = COALESCE(lesson_progress.completed_at_utc,
                                            excluded.completed_at_utc)""",
                (user_id, lesson.id, section, now, completed))

    def last_studied(self, user_id: int) -> tuple[str, int] | None:
        with self._connect() as connection:
            row = connection.execute("""SELECT lesson_id, last_section FROM lesson_progress
                WHERE user_id = ? ORDER BY last_studied_at_utc DESC LIMIT 1""",
                (user_id,)).fetchone()
        return (row["lesson_id"], row["last_section"]) if row else None

    def lesson_progress(self, user_id: int, lesson_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM lesson_progress
                WHERE user_id = ? AND lesson_id = ?""", (user_id, lesson_id)).fetchone()

    def start_attempt(self, user_id: int, lesson: Lesson) -> int:
        with self._connect() as connection:
            now = self._now()
            connection.execute("""INSERT INTO lesson_progress
                (user_id, lesson_id, last_section, last_studied_at_utc)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(user_id, lesson_id) DO UPDATE SET
                last_studied_at_utc = excluded.last_studied_at_utc""",
                (user_id, lesson.id, now))
            cursor = connection.execute("""INSERT INTO quiz_attempts
                (user_id, lesson_id, current_index, started_at_utc)
                VALUES (?, ?, 0, ?)""", (user_id, lesson.id, now))
            return int(cursor.lastrowid)

    def attempt(self, user_id: int, attempt_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM quiz_attempts
                WHERE id = ? AND user_id = ?""", (attempt_id, user_id)).fetchone()

    def active_attempt(self, user_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM quiz_attempts
                WHERE user_id = ? AND completed_at_utc IS NULL
                ORDER BY id DESC LIMIT 1""", (user_id,)).fetchone()

    def current_run(self, user_id: int, attempt_id: int, index: int) -> sqlite3.Row | None:
        if self.attempt(user_id, attempt_id) is None:
            return None
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM question_runs
                WHERE attempt_id = ? AND question_index = ?
                ORDER BY run_number DESC LIMIT 1""", (attempt_id, index)).fetchone()

    def ensure_run(self, user_id: int, attempt_id: int, index: int) -> sqlite3.Row:
        attempt = self.attempt(user_id, attempt_id)
        if attempt is None or attempt["completed_at_utc"] or attempt["current_index"] != index:
            raise ValueError("Outdated quiz question")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("""SELECT * FROM question_runs WHERE attempt_id = ?
                AND question_index = ? ORDER BY run_number DESC LIMIT 1""",
                (attempt_id, index)).fetchone()
            if row is None:
                now = self._now()
                cursor = connection.execute("""INSERT INTO question_runs
                    (attempt_id, question_index, run_number, created_at_utc, updated_at_utc)
                    VALUES (?, ?, 1, ?, ?)""", (attempt_id, index, now, now))
                row = connection.execute("SELECT * FROM question_runs WHERE id = ?",
                                         (cursor.lastrowid,)).fetchone()
            return row

    def use_hint(self, user_id: int, attempt_id: int, run_id: int) -> sqlite3.Row | None:
        attempt = self.attempt(user_id, attempt_id)
        if attempt is None or attempt["completed_at_utc"]:
            return None
        with self._connect() as connection:
            changed = connection.execute("""UPDATE question_runs
                SET hints_used = hints_used + 1, updated_at_utc = ?
                WHERE id = ? AND attempt_id = ? AND question_index = ?
                AND submitted_answer IS NULL AND hints_used < 3""",
                (self._now(), run_id, attempt_id, attempt["current_index"])).rowcount
            if not changed:
                return None
            return connection.execute("SELECT * FROM question_runs WHERE id = ?",
                                      (run_id,)).fetchone()

    def submit(self, user_id: int, attempt_id: int, run_id: int,
               answer: str, grade: Grade) -> bool:
        attempt = self.attempt(user_id, attempt_id)
        if attempt is None or attempt["completed_at_utc"]:
            return False
        with self._connect() as connection:
            changed = connection.execute("""UPDATE question_runs
                SET submitted_answer = ?, points = ?, feedback = ?, updated_at_utc = ?
                WHERE id = ? AND attempt_id = ? AND question_index = ?
                AND submitted_answer IS NULL AND revealed = 0""",
                (answer, grade.points, grade.feedback, self._now(), run_id, attempt_id,
                 attempt["current_index"])).rowcount
            return bool(changed)

    def reveal(self, user_id: int, attempt_id: int, run_id: int) -> bool:
        attempt = self.attempt(user_id, attempt_id)
        if attempt is None or attempt["completed_at_utc"]:
            return False
        with self._connect() as connection:
            changed = connection.execute("""UPDATE question_runs SET revealed = 1,
                submitted_answer = '[shown answer]', points = 0,
                feedback = 'Ответ показан; балл за эту попытку равен 0.', updated_at_utc = ?
                WHERE id = ? AND attempt_id = ? AND question_index = ?
                AND submitted_answer IS NULL AND hints_used = 3""",
                (self._now(), run_id, attempt_id, attempt["current_index"])).rowcount
            return bool(changed)

    def retry_question(self, user_id: int, attempt_id: int, run_id: int) -> sqlite3.Row | None:
        attempt = self.attempt(user_id, attempt_id)
        if attempt is None or attempt["completed_at_utc"]:
            return None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute("""SELECT * FROM question_runs
                WHERE attempt_id = ? AND question_index = ?
                ORDER BY run_number DESC LIMIT 1""",
                (attempt_id, attempt["current_index"])).fetchone()
            if latest is None or latest["id"] != run_id or latest["submitted_answer"] is None:
                return None
            now = self._now()
            cursor = connection.execute("""INSERT INTO question_runs
                (attempt_id, question_index, run_number, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?)""", (attempt_id, attempt["current_index"],
                                             latest["run_number"] + 1, now, now))
            return connection.execute("SELECT * FROM question_runs WHERE id = ?",
                                      (cursor.lastrowid,)).fetchone()

    def advance(self, user_id: int, attempt_id: int, run_id: int) -> bool:
        attempt = self.attempt(user_id, attempt_id)
        if attempt is None or attempt["completed_at_utc"]:
            return False
        latest = self.current_run(user_id, attempt_id, attempt["current_index"])
        if latest is None or latest["id"] != run_id or latest["submitted_answer"] is None:
            return False
        lesson = self.catalog.lesson(attempt["lesson_id"])
        if lesson is None:
            return False
        index = attempt["current_index"]
        if index + 1 < len(lesson.questions):
            with self._connect() as connection:
                return bool(connection.execute("""UPDATE quiz_attempts
                    SET current_index = ? WHERE id = ? AND user_id = ?
                    AND current_index = ? AND completed_at_utc IS NULL""",
                    (index + 1, attempt_id, user_id, index)).rowcount)
        self._finish_attempt(user_id, attempt_id, lesson)
        return True

    def _finish_attempt(self, user_id: int, attempt_id: int, lesson: Lesson) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            attempt = connection.execute("""SELECT * FROM quiz_attempts
                WHERE id = ? AND user_id = ?""", (attempt_id, user_id)).fetchone()
            if attempt is None or attempt["completed_at_utc"]:
                return
            runs = connection.execute("""SELECT * FROM question_runs
                WHERE attempt_id = ? ORDER BY question_index, run_number""",
                (attempt_id,)).fetchall()
            latest = {row["question_index"]: row for row in runs}
            if len(latest) != len(lesson.questions) or any(
                row["submitted_answer"] is None for row in latest.values()
            ):
                raise ValueError("Quiz has unanswered questions")
            points = round(sum(row["points"] for row in latest.values()), 2)
            now = self._now()
            connection.execute("""UPDATE quiz_attempts SET current_index = ?,
                completed_at_utc = ?, total_points = ? WHERE id = ?""",
                (len(lesson.questions), now, points, attempt_id))
            if points / len(lesson.questions) * 100 >= self.mastery_threshold_percent:
                connection.execute("""INSERT INTO lesson_progress
                    (user_id, lesson_id, last_section, last_studied_at_utc, mastered_at_utc)
                    VALUES (?, ?, 0, ?, ?)
                    ON CONFLICT(user_id, lesson_id) DO UPDATE SET
                    mastered_at_utc = COALESCE(lesson_progress.mastered_at_utc,
                                               excluded.mastered_at_utc)""",
                    (user_id, lesson.id, now, now))
            module = self.catalog.module_by_id[lesson.module_id]
            placeholders = ",".join("?" for _ in module.lessons)
            mastered = connection.execute(
                f"""SELECT COUNT(*) FROM lesson_progress WHERE user_id = ?
                AND lesson_id IN ({placeholders}) AND mastered_at_utc IS NOT NULL""",
                (user_id, *(entry.id for entry in module.lessons)),
            ).fetchone()[0]
            connection.execute("""INSERT INTO module_mastery
                (user_id, module_id, mastered_lessons, total_lessons, updated_at_utc)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, module_id) DO UPDATE SET
                mastered_lessons = excluded.mastered_lessons,
                total_lessons = excluded.total_lessons,
                updated_at_utc = excluded.updated_at_utc""",
                (user_id, module.id, mastered, len(module.lessons), now))

    def attempt_runs(self, user_id: int, attempt_id: int) -> list[sqlite3.Row]:
        if self.attempt(user_id, attempt_id) is None:
            return []
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM question_runs
                WHERE attempt_id = ? ORDER BY question_index, run_number""",
                (attempt_id,)).fetchall()

    def completed_attempts(self, user_id: int, lesson_id: str | None = None) -> list[sqlite3.Row]:
        sql = """SELECT * FROM quiz_attempts WHERE user_id = ? AND completed_at_utc IS NOT NULL"""
        params: tuple = (user_id,)
        if lesson_id is not None:
            sql += " AND lesson_id = ?"
            params += (lesson_id,)
        sql += " ORDER BY id DESC"
        with self._connect() as connection:
            return connection.execute(sql, params).fetchall()

    def progress_snapshot(self, user_id: int) -> dict:
        with self._connect() as connection:
            lessons = connection.execute("SELECT * FROM lesson_progress WHERE user_id = ?",
                                         (user_id,)).fetchall()
            attempts = connection.execute("""SELECT * FROM quiz_attempts
                WHERE user_id = ? AND completed_at_utc IS NOT NULL""", (user_id,)).fetchall()
            runs = connection.execute("""SELECT qr.* FROM question_runs qr
                JOIN quiz_attempts qa ON qa.id = qr.attempt_id
                WHERE qa.user_id = ?""",
                (user_id,)).fetchall()
            mastery = connection.execute("SELECT * FROM module_mastery WHERE user_id = ?",
                                         (user_id,)).fetchall()
        return {"lessons": lessons, "attempts": attempts,
                "answers": [row for row in runs if row["submitted_answer"] is not None],
                "runs": runs,
                "modules": mastery, "last_studied": self.last_studied(user_id)}
