from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cyberpath_ai.academy.adaptive import AdaptiveStore
from cyberpath_ai.academy.content import load_catalog
from cyberpath_ai.academy.grading import Grade, grade
from cyberpath_ai.academy.storage import AcademyStore


T0 = datetime(2026, 10, 21, 12, tzinfo=UTC)


def setup(tmp_path: Path, now: datetime = T0):
    catalog = load_catalog()
    path = tmp_path / "academy.sqlite3"
    academy = AcademyStore(path, catalog)
    current = [now]
    adaptive = AdaptiveStore(path, catalog, clock=lambda: current[0])
    return academy, adaptive, current


def evidence(adaptive: AdaptiveStore, user: int, question: str, source: str,
             points: float, hints: int, revealed: bool, at: datetime,
             kind: str = "quiz"):
    with adaptive._connect() as connection:
        return adaptive._record(connection, user, question, source, kind, points, hints,
                                revealed, at)


def test_skill_percent_partial_hints_reveal_and_not_assessed(tmp_path: Path):
    _, adaptive, _ = setup(tmp_path)
    statuses = {row.skill.id: row for row in adaptive.skill_statuses(42)}
    assert statuses["private_ipv4"].percent is None
    evidence(adaptive, 42, "ip_q2", "quiz:a", 1, 0, False, T0)
    evidence(adaptive, 42, "ip_q5", "quiz:b", .5, 2, False, T0)
    status = {row.skill.id: row for row in adaptive.skill_statuses(42)}["private_ipv4"]
    assert status.assessed == 2
    assert status.percent == 68  # (1.0 + 0.5 * (1 - 2 * .15)) / 2
    evidence(adaptive, 42, "ip_q2", "review:c", 0, 3, True, T0 + timedelta(days=1), "review")
    assert {row.skill.id: row for row in adaptive.skill_statuses(42)}["private_ipv4"].percent == 18
    assert not evidence(adaptive, 42, "ip_q2", "review:c", 1, 0, False,
                        T0 + timedelta(days=2), "review")
    assert len(adaptive.latest_evidence(42)) == 2
    assert adaptive.skill_statuses(99)[0].percent is None


def test_review_intervals_due_overdue_and_timezone(tmp_path: Path):
    _, adaptive, current = setup(tmp_path)
    evidence(adaptive, 42, "ip_q1", "quiz:1", 1, 0, False, T0)
    first = adaptive.schedules(42)[0]
    assert (first["interval_days"], first["success_streak"], first["latest_outcome"]) == (3, 1, "independent")
    assert adaptive.due_questions(42) == []
    current[0] = T0 + timedelta(days=3)
    assert len(adaptive.due_questions(42)) == 1
    evidence(adaptive, 42, "ip_q1", "review:1", 1, 0, False, current[0], "review")
    assert (adaptive.schedules(42)[0]["interval_days"],
            adaptive.schedules(42)[0]["success_streak"]) == (7, 2)
    current[0] += timedelta(days=7)
    evidence(adaptive, 42, "ip_q1", "review:2", 1, 0, False, current[0], "review")
    assert adaptive.schedules(42)[0]["interval_days"] == 14
    current[0] += timedelta(days=14)
    evidence(adaptive, 42, "ip_q1", "review:3", 1, 0, False, current[0], "review")
    assert adaptive.schedules(42)[0]["interval_days"] == 30
    current[0] += timedelta(days=30, hours=2)
    assert len(adaptive.due_questions(42)) == 1  # overdue is still due
    evidence(adaptive, 42, "ip_q1", "review:4", 1, 1, False, current[0], "review")
    failed = adaptive.schedules(42)[0]
    assert (failed["interval_days"], failed["success_streak"], failed["latest_outcome"]) == (1, 0, "hinted")
    assert adaptive.local_due(42, failed["next_due_at_utc"]) == (
        datetime.fromisoformat(failed["next_due_at_utc"]).astimezone(
            ZoneInfo("Asia/Almaty")).strftime("%d.%m.%Y %H:%M") + " (Asia/Almaty)")


def test_recommendation_uses_prerequisites_then_recent_mistakes(tmp_path: Path):
    academy, adaptive, _ = setup(tmp_path)
    assert adaptive.recommend(42).lesson.id == "ip_subnets"
    recent = T0 - timedelta(hours=12)
    evidence(adaptive, 42, "tu_q1", "quiz:1", 0, 0, False, recent)
    evidence(adaptive, 42, "tu_q2", "quiz:2", 0, 0, False, recent)
    blocked = adaptive.recommend(42)
    assert blocked.lesson.id == "ip_subnets"
    assert "полезно" in blocked.reason
    lesson = academy.catalog.lesson("ip_subnets")
    academy.view_section(42, lesson, len(lesson.sections) + 1)
    recommended = adaptive.recommend(42)
    assert recommended.lesson.id == "tcp_udp"
    assert "2 вопрос" in recommended.reason


def test_legacy_quiz_migration_preserves_history_and_is_idempotent(tmp_path: Path):
    catalog = load_catalog()
    path = tmp_path / "old.sqlite3"
    academy = AcademyStore(path, catalog)
    lesson = catalog.lesson("ip_subnets")
    academy.view_section(42, lesson, len(lesson.sections) + 1)
    attempt_id = academy.start_attempt(42, lesson)
    for index, question in enumerate(lesson.questions):
        run = academy.ensure_run(42, attempt_id, index)
        answer = (question["answer"] if question["kind"] == "choice" else
                  question["accepted"][0] if question["kind"] == "text" else
                  ", ".join(part["aliases"][0] for part in question["parts"]))
        assert academy.submit(42, attempt_id, run["id"], answer, grade(question, answer))
        assert academy.advance(42, attempt_id, run["id"])
    before = len(academy.attempt_runs(42, attempt_id))
    adaptive = AdaptiveStore(path, catalog)
    assert len(adaptive.latest_evidence(42)) == 5
    assert len(adaptive.schedules(42)) == 5
    reopened = AdaptiveStore(path, catalog)
    assert len(reopened.latest_evidence(42)) == 5
    assert len(academy.attempt_runs(42, attempt_id)) == before
    assert academy.lesson_progress(42, lesson.id)["completed_at_utc"]
    assert reopened.skill_statuses(99)[0].percent is None


def test_review_session_resume_hint_reveal_retry_and_duplicate_guard(tmp_path: Path):
    academy, adaptive, current = setup(tmp_path)
    evidence(adaptive, 42, "ip_q1", "quiz:1", 0, 0, False, T0 - timedelta(days=2))
    evidence(adaptive, 42, "ip_q2", "quiz:2", 1, 0, False, T0 - timedelta(days=4))
    assert [row["question_id"] for row in adaptive.due_questions(42)] == ["ip_q1", "ip_q2"]
    session_id = adaptive.start_review_session(42)
    assert adaptive.start_review_session(42) == session_id
    assert adaptive.start_review_session(99) is None
    reopened = AdaptiveStore(academy.path, academy.catalog, clock=lambda: current[0])
    assert reopened.active_review_session(42)["id"] == session_id
    assert reopened.review_session(99, session_id) is None
    run = reopened.ensure_review_run(42, session_id, 0)
    assert not reopened.review_reveal(42, session_id, run["id"])
    for count in (1, 2, 3):
        assert reopened.review_hint(42, session_id, run["id"])["hints_used"] == count
    assert reopened.review_reveal(42, session_id, run["id"])
    assert not reopened.review_submit(42, session_id, run["id"], "b", Grade(1, "", True))
    assert reopened.review_advance(42, session_id, run["id"])
    assert not reopened.review_advance(42, session_id, run["id"])
    schedule = {row["question_id"]: row for row in reopened.schedules(42)}["ip_q1"]
    assert (schedule["latest_outcome"], schedule["interval_days"], schedule["success_streak"]) == (
        "revealed", 1, 0)
    assert reopened.review_session(42, session_id)["current_index"] == 1
    second = reopened.ensure_review_run(42, session_id, 1)
    question = academy.catalog.question_by_id["ip_q2"][2]
    assert reopened.review_submit(42, session_id, second["id"], "a", grade(question, "a"))
    retry = reopened.review_retry(42, session_id, second["id"])
    assert retry["run_number"] == 2
    assert reopened.review_submit(42, session_id, retry["id"], "a", grade(question, "a"))
    assert reopened.review_advance(42, session_id, retry["id"])
    assert reopened.review_session(42, session_id)["completed_at_utc"]
    assert len(reopened.review_results(42, session_id)) == 2
    assert {row["question_id"]: row for row in reopened.schedules(42)}["ip_q2"]["interval_days"] == 7
