"""Additive SQLite storage for investigation attempts, independent of quizzes."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from cyberpath_ai.academy.investigations import InvestigationCatalog, grade_step


class InvestigationStore:
    def __init__(self, path: Path, catalog: InvestigationCatalog,
                 *, clock: Callable[[], datetime] | None = None):
        self.path = path
        self.catalog = catalog
        self.clock = clock or (lambda: datetime.now(UTC))
        self._initialize()

    def _now(self) -> str:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Investigation clock must be timezone-aware")
        return value.astimezone(UTC).isoformat()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS investigation_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, scenario_id TEXT NOT NULL,
                current_index INTEGER NOT NULL DEFAULT 0,
                started_at_utc TEXT NOT NULL, completed_at_utc TEXT,
                total_points REAL,
                CHECK(current_index >= 0)
            )""")
            connection.execute("""CREATE INDEX IF NOT EXISTS investigation_user_idx
                ON investigation_attempts(user_id, scenario_id, id)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS investigation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id INTEGER NOT NULL REFERENCES investigation_attempts(id),
                step_index INTEGER NOT NULL, run_number INTEGER NOT NULL,
                hints_used INTEGER NOT NULL DEFAULT 0 CHECK(hints_used BETWEEN 0 AND 3),
                revealed INTEGER NOT NULL DEFAULT 0 CHECK(revealed IN (0, 1)),
                submitted_answer TEXT, accepted INTEGER NOT NULL DEFAULT 0
                    CHECK(accepted IN (0, 1)),
                points REAL, feedback TEXT,
                created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL,
                UNIQUE(attempt_id, step_index, run_number)
            )""")

    def start(self, user_id: int, scenario_id: str) -> int:
        if scenario_id not in self.catalog.by_id:
            raise ValueError("Unknown scenario")
        with self._connect() as connection:
            cursor = connection.execute("""INSERT INTO investigation_attempts
                (user_id, scenario_id, started_at_utc) VALUES (?, ?, ?)""",
                (user_id, scenario_id, self._now()))
            return int(cursor.lastrowid)

    def start_or_resume(self, user_id: int, scenario_id: str) -> int:
        if scenario_id not in self.catalog.by_id:
            raise ValueError("Unknown scenario")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute("""SELECT id FROM investigation_attempts
                WHERE user_id=? AND scenario_id=? AND completed_at_utc IS NULL
                ORDER BY id DESC LIMIT 1""", (user_id, scenario_id)).fetchone()
            if active:
                return active["id"]
            cursor = connection.execute("""INSERT INTO investigation_attempts
                (user_id, scenario_id, started_at_utc) VALUES (?, ?, ?)""",
                (user_id, scenario_id, self._now()))
            return int(cursor.lastrowid)

    def attempt(self, user_id: int, attempt_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM investigation_attempts
                WHERE id=? AND user_id=?""", (attempt_id, user_id)).fetchone()

    def active(self, user_id: int, scenario_id: str | None = None) -> sqlite3.Row | None:
        sql = """SELECT * FROM investigation_attempts WHERE user_id=?
            AND completed_at_utc IS NULL"""
        parameters: tuple = (user_id,)
        if scenario_id is not None:
            sql += " AND scenario_id=?"
            parameters += (scenario_id,)
        with self._connect() as connection:
            return connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()

    def latest(self, user_id: int, scenario_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM investigation_attempts
                WHERE user_id=? AND scenario_id=? ORDER BY id DESC LIMIT 1""",
                (user_id, scenario_id)).fetchone()

    def _latest_run(self, connection: sqlite3.Connection, attempt_id: int,
                    step_index: int) -> sqlite3.Row | None:
        return connection.execute("""SELECT * FROM investigation_runs
            WHERE attempt_id=? AND step_index=? ORDER BY run_number DESC LIMIT 1""",
            (attempt_id, step_index)).fetchone()

    def current_run(self, user_id: int, attempt_id: int) -> sqlite3.Row | None:
        attempt = self.attempt(user_id, attempt_id)
        if attempt is None or attempt["completed_at_utc"]:
            return None
        with self._connect() as connection:
            return self._latest_run(connection, attempt_id, attempt["current_index"])

    def ensure_run(self, user_id: int, attempt_id: int) -> sqlite3.Row:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            attempt = connection.execute("""SELECT * FROM investigation_attempts
                WHERE id=? AND user_id=?""", (attempt_id, user_id)).fetchone()
            if attempt is None or attempt["completed_at_utc"]:
                raise ValueError("Outdated investigation")
            index = attempt["current_index"]
            if index >= len(self.catalog.by_id[attempt["scenario_id"]].steps):
                raise ValueError("Invalid investigation position")
            run = self._latest_run(connection, attempt_id, index)
            if run is None:
                now = self._now()
                cursor = connection.execute("""INSERT INTO investigation_runs
                    (attempt_id, step_index, run_number, created_at_utc, updated_at_utc)
                    VALUES (?, ?, 1, ?, ?)""", (attempt_id, index, now, now))
                run = connection.execute("SELECT * FROM investigation_runs WHERE id=?",
                                         (cursor.lastrowid,)).fetchone()
            return run

    def _current(self, connection: sqlite3.Connection, user_id: int,
                 attempt_id: int, run_id: int) -> tuple[sqlite3.Row, sqlite3.Row] | None:
        attempt = connection.execute("""SELECT * FROM investigation_attempts
            WHERE id=? AND user_id=? AND completed_at_utc IS NULL""",
            (attempt_id, user_id)).fetchone()
        if attempt is None:
            return None
        run = self._latest_run(connection, attempt_id, attempt["current_index"])
        return (attempt, run) if run is not None and run["id"] == run_id else None

    def hint(self, user_id: int, attempt_id: int, run_id: int,
             expected_hints: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current(connection, user_id, attempt_id, run_id)
            if (current is None or current[1]["submitted_answer"] is not None or
                    current[1]["hints_used"] >= 3 or
                    current[1]["hints_used"] != expected_hints):
                return None
            connection.execute("""UPDATE investigation_runs SET hints_used=hints_used+1,
                updated_at_utc=? WHERE id=?""", (self._now(), run_id))
            return connection.execute("SELECT * FROM investigation_runs WHERE id=?",
                                      (run_id,)).fetchone()

    def submit(self, user_id: int, attempt_id: int, run_id: int, answer: str) -> bool:
        if not 0 < len(answer) <= 250:
            return False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current(connection, user_id, attempt_id, run_id)
            if current is None or current[1]["submitted_answer"] is not None or current[1]["revealed"]:
                return False
            attempt, _ = current
            step = self.catalog.by_id[attempt["scenario_id"]].steps[attempt["current_index"]]
            result = grade_step(step, answer)
            was_shown = connection.execute("""SELECT 1 FROM investigation_runs
                WHERE attempt_id=? AND step_index=? AND revealed=1 LIMIT 1""",
                (attempt_id, attempt["current_index"])).fetchone() is not None
            points = 0 if was_shown else result.points
            feedback = ("Ответ верный, но решение уже было показано; "
                        "самостоятельный балл за этот шаг равен 0."
                        if was_shown and result.correct else result.feedback)
            return bool(connection.execute("""UPDATE investigation_runs SET
                submitted_answer=?, accepted=?, points=?, feedback=?, updated_at_utc=?
                WHERE id=? AND submitted_answer IS NULL""",
                (answer, int(result.correct), points, feedback, self._now(), run_id)).rowcount)

    def reveal(self, user_id: int, attempt_id: int, run_id: int) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current(connection, user_id, attempt_id, run_id)
            if current is None or current[1]["submitted_answer"] is not None or current[1]["hints_used"] != 3:
                return False
            return bool(connection.execute("""UPDATE investigation_runs SET
                revealed=1, submitted_answer='[shown answer]', points=0,
                feedback='Ответ показан: самостоятельное решение не засчитано.',
                updated_at_utc=? WHERE id=?""", (self._now(), run_id)).rowcount)

    def retry(self, user_id: int, attempt_id: int, run_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current(connection, user_id, attempt_id, run_id)
            if current is None or current[1]["submitted_answer"] is None:
                return None
            attempt, old = current
            now = self._now()
            cursor = connection.execute("""INSERT INTO investigation_runs
                (attempt_id, step_index, run_number, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?)""",
                (attempt_id, attempt["current_index"], old["run_number"] + 1, now, now))
            return connection.execute("SELECT * FROM investigation_runs WHERE id=?",
                                      (cursor.lastrowid,)).fetchone()

    def advance(self, user_id: int, attempt_id: int, run_id: int) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current(connection, user_id, attempt_id, run_id)
            if current is None or current[1]["submitted_answer"] is None:
                return False
            attempt, _ = current
            scenario = self.catalog.by_id[attempt["scenario_id"]]
            next_index = attempt["current_index"] + 1
            if next_index < len(scenario.steps):
                connection.execute("""UPDATE investigation_attempts SET current_index=?
                    WHERE id=?""", (next_index, attempt_id))
            else:
                rows = connection.execute("""SELECT * FROM investigation_runs
                    WHERE attempt_id=? ORDER BY step_index, run_number""",
                    (attempt_id,)).fetchall()
                latest = {row["step_index"]: row for row in rows}
                if len(latest) != len(scenario.steps) or any(
                    row["submitted_answer"] is None for row in latest.values()
                ):
                    raise ValueError("Investigation has unfinished steps")
                connection.execute("""UPDATE investigation_attempts SET current_index=?,
                    completed_at_utc=?, total_points=? WHERE id=?""",
                    (next_index, self._now(), sum(row["points"] for row in latest.values()), attempt_id))
            return True

    def runs(self, user_id: int, attempt_id: int) -> list[sqlite3.Row]:
        if self.attempt(user_id, attempt_id) is None:
            return []
        with self._connect() as connection:
            return connection.execute("""SELECT * FROM investigation_runs
                WHERE attempt_id=? ORDER BY step_index, run_number""",
                (attempt_id,)).fetchall()

    def summary(self, user_id: int) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT completed_at_utc FROM investigation_attempts
                WHERE user_id=?""", (user_id,)).fetchall()
        return {"completed": sum(row["completed_at_utc"] is not None for row in rows),
                "active": sum(row["completed_at_utc"] is None for row in rows)}
