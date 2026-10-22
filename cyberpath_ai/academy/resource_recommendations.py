"""Offline recommendation rules and additive resource activity storage."""

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

from cyberpath_ai.academy.adaptive import AdaptiveStore
from cyberpath_ai.academy.content import Catalog
from cyberpath_ai.academy.resources import Resource, ResourceCatalog
from cyberpath_ai.academy.storage import AcademyStore


@dataclass(frozen=True)
class ResourceSuggestion:
    resource: Resource
    reason: str


class ResourceStore:
    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None):
        self.path = path
        self.clock = clock or (lambda: datetime.now(UTC))
        self._initialize()

    @contextmanager
    def _connect(self):
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
            connection.execute("""CREATE TABLE IF NOT EXISTS resource_activity (
                user_id INTEGER NOT NULL, resource_id TEXT NOT NULL,
                recommended_at_utc TEXT, recommendation_count INTEGER NOT NULL DEFAULT 0,
                opened_at_utc TEXT, completed_at_utc TEXT, note TEXT,
                PRIMARY KEY(user_id, resource_id)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS resource_preferences (
                user_id INTEGER PRIMARY KEY, difficulty TEXT NOT NULL DEFAULT 'Beginner'
            )""")

    def get(self, user_id: int, resource_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM resource_activity WHERE user_id=? AND resource_id=?",
                                      (user_id, resource_id)).fetchone()

    def activity(self, user_id: int) -> dict[str, sqlite3.Row]:
        with self._connect() as connection:
            return {row["resource_id"]: row for row in connection.execute(
                "SELECT * FROM resource_activity WHERE user_id=?", (user_id,))}

    def difficulty(self, user_id: int) -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT difficulty FROM resource_preferences WHERE user_id=?",
                                     (user_id,)).fetchone()
        return row["difficulty"] if row else "Beginner"

    def set_difficulty(self, user_id: int, difficulty: str) -> None:
        if difficulty not in {"Beginner", "Intermediate", "Advanced"}:
            raise ValueError("Invalid difficulty")
        with self._connect() as connection:
            connection.execute("""INSERT INTO resource_preferences(user_id, difficulty) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET difficulty=excluded.difficulty""",
                (user_id, difficulty))

    def recommended(self, user_id: int, resource_ids: list[str]) -> None:
        if not resource_ids:
            return
        timestamp = self.clock().astimezone(UTC).isoformat()
        with self._connect() as connection:
            for resource_id in dict.fromkeys(resource_ids):
                connection.execute("""INSERT INTO resource_activity
                    (user_id, resource_id, recommended_at_utc, recommendation_count)
                    VALUES (?, ?, ?, 1) ON CONFLICT(user_id, resource_id) DO UPDATE SET
                    recommended_at_utc=excluded.recommended_at_utc,
                    recommendation_count=resource_activity.recommendation_count+1""",
                    (user_id, resource_id, timestamp))

    def mark_opened(self, user_id: int, resource_id: str) -> None:
        timestamp = self.clock().astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("""INSERT INTO resource_activity(user_id, resource_id, opened_at_utc)
                VALUES (?, ?, ?) ON CONFLICT(user_id, resource_id) DO UPDATE SET
                opened_at_utc=COALESCE(resource_activity.opened_at_utc, excluded.opened_at_utc)""",
                (user_id, resource_id, timestamp))

    def mark_completed(self, user_id: int, resource_id: str) -> None:
        timestamp = self.clock().astimezone(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("""INSERT INTO resource_activity(user_id, resource_id, completed_at_utc)
                VALUES (?, ?, ?) ON CONFLICT(user_id, resource_id) DO UPDATE SET
                completed_at_utc=COALESCE(resource_activity.completed_at_utc, excluded.completed_at_utc)""",
                (user_id, resource_id, timestamp))

    def set_note(self, user_id: int, resource_id: str, note: str | None) -> None:
        if note is not None and len(note) > 500:
            raise ValueError("Resource note too long")
        with self._connect() as connection:
            connection.execute("""INSERT INTO resource_activity(user_id, resource_id, note)
                VALUES (?, ?, ?) ON CONFLICT(user_id, resource_id) DO UPDATE SET note=excluded.note""",
                (user_id, resource_id, note))


class ResourceRecommender:
    def __init__(self, catalog: Catalog, resources: ResourceCatalog, academy: AcademyStore,
                 adaptive: AdaptiveStore, activity: ResourceStore):
        self.catalog = catalog
        self.resources = resources
        self.academy = academy
        self.adaptive = adaptive
        self.activity = activity

    def module_completed(self, user_id: int, module_id: str) -> bool:
        module = self.catalog.module_by_id[module_id]
        return all((row := self.academy.lesson_progress(user_id, lesson.id)) is not None
                   and row["completed_at_utc"] is not None for lesson in module.lessons)

    def recommend(self, user_id: int, lesson_id: str, *, category: str | tuple[str, ...] | None = None,
                  limit: int = 3) -> list[ResourceSuggestion]:
        lesson = self.catalog.lesson(lesson_id)
        if lesson is None or limit < 1:
            return []
        module_done = self.module_completed(user_id, lesson.module_id)
        module = self.catalog.module_by_id[lesson.module_id]
        scoped_lessons = {item.id for item in module.lessons} if module_done else {lesson_id}
        statuses = {status.skill.id: status for status in self.adaptive.skill_statuses(user_id)}
        latest = self.adaptive.latest_evidence(user_id)
        cutoff = datetime.now(UTC) - timedelta(days=7)
        recent_mistakes = set()
        for row in latest.values():
            if (datetime.fromisoformat(row["occurred_at_utc"]) >= cutoff and
                    self.adaptive.effective_points(row["points"], row["hints_used"],
                                                   bool(row["revealed"])) < 0.8):
                recent_mistakes.add(row["skill_id"])
        activity = self.activity.activity(user_id)
        preferred = self.activity.difficulty(user_id)
        rank = {"Beginner": 0, "Intermediate": 1, "Advanced": 2}
        all_completed = {item.id for item in self.catalog.lesson_by_id.values() if
                         (record := self.academy.lesson_progress(user_id, item.id)) is not None
                         and record["completed_at_utc"] is not None}
        candidates = []
        for resource in self.resources.resources:
            if category and resource.category not in ((category,) if isinstance(category, str) else category):
                continue
            if not scoped_lessons.intersection(resource.lesson_ids):
                continue
            if not set(resource.prerequisite_lesson_ids).issubset(all_completed):
                continue
            record = activity.get(resource.id)
            if record and record["completed_at_utc"]:
                continue
            # Advanced material is held until the user chooses that level.
            if rank[resource.difficulty] > rank[preferred] + 1:
                continue
            related = [statuses[skill_id] for skill_id in resource.skill_ids
                       if skill_id in statuses and statuses[skill_id].skill.lesson_id in scoped_lessons]
            weak = sorted((status for status in related if status.percent is not None
                           and status.percent < self.adaptive.threshold),
                          key=lambda item: (item.percent, item.skill.id))
            mastered = [status for status in related if status.percent is not None
                        and status.percent >= self.adaptive.threshold
                        and status.assessed == status.total]
            if weak:
                reason = (f"Тема «{weak[0].skill.title}» оценена на {weak[0].percent}%; "
                          "этот материал поможет её закрепить.")
            elif mastered and resource.category in {"labs", "practice"}:
                reason = "Проверенная тема освоена; это следующий шаг для практики в учебной среде."
            elif any(status.skill.id in recent_mistakes for status in related):
                reason = "Поможет повторить тему после недавних ошибок в квизе."
            else:
                reason = resource.why
            score = 30 if resource.access == "Free" else 5 if resource.access == "Unknown" else -20
            score += 10 if resource.difficulty == preferred else 0
            score += 6 if lesson_id in resource.lesson_ids else 0
            score += 24 if weak else 0
            score += 12 if weak and resource.category in {"video", "reading"} else 0
            score += 15 if mastered and resource.category in {"labs", "practice"} else 0
            score += 10 if recent_mistakes.intersection(resource.skill_ids) else 0
            score += 30 if module_done and resource.path else 0
            score -= 15 if resource.path and not module_done else 0
            score -= 8 * (record["recommendation_count"] if record else 0)
            score -= 12 if record and record["opened_at_utc"] else 0
            candidates.append((-score, resource.id, ResourceSuggestion(resource, reason)))
        candidates.sort(key=lambda item: (item[0], item[1]))
        chosen = [item[2] for item in candidates[:limit]]
        if module_done and not category:
            paths = [item[2] for item in candidates if item[2].resource.path]
            if paths and all(not item.resource.path for item in chosen):
                chosen[-1:] = paths[:1]
        return chosen
