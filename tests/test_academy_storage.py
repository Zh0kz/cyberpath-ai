from pathlib import Path

from cyberpath_ai.academy.content import load_catalog
from cyberpath_ai.academy.grading import Grade, grade
from cyberpath_ai.academy.storage import AcademyStore


def _store(tmp_path: Path) -> AcademyStore:
    return AcademyStore(tmp_path / "academy.sqlite3", load_catalog())


def test_reading_completion_resume_and_restart(tmp_path: Path):
    store = _store(tmp_path)
    lesson = store.catalog.lesson("ip_subnets")
    store.view_section(42, lesson, 2)
    assert store.lesson_progress(42, lesson.id)["completed_at_utc"] is None
    assert store.last_studied(42) == (lesson.id, 2)
    store.start_attempt(42, lesson)
    assert store.last_studied(42) == (lesson.id, 2)
    reopened = _store(tmp_path)
    assert reopened.last_studied(42) == (lesson.id, 2)
    reopened.view_section(42, lesson, len(lesson.sections) + 1)
    assert reopened.lesson_progress(42, lesson.id)["completed_at_utc"]
    assert reopened.lesson_progress(42, lesson.id)["mastered_at_utc"] is None
    assert reopened.lesson_progress(99, lesson.id) is None


def test_hints_reveal_retry_and_duplicate_submission(tmp_path: Path):
    store = _store(tmp_path)
    lesson = store.catalog.lesson("ip_subnets")
    attempt_id = store.start_attempt(42, lesson)
    run = store.ensure_run(42, attempt_id, 0)
    assert not store.reveal(42, attempt_id, run["id"])
    for count in range(1, 4):
        assert store.use_hint(42, attempt_id, run["id"])["hints_used"] == count
    assert sum(row["hints_used"] for row in store.progress_snapshot(42)["runs"]) == 3
    assert store.use_hint(42, attempt_id, run["id"]) is None
    assert store.reveal(42, attempt_id, run["id"])
    assert not store.reveal(42, attempt_id, run["id"])
    assert not store.submit(42, attempt_id, run["id"], "b", Grade(1, "", True))
    assert store.current_run(42, attempt_id, 0)["points"] == 0
    retried = store.retry_question(42, attempt_id, run["id"])
    assert retried["run_number"] == 2
    assert store.submit(42, attempt_id, retried["id"], "b", grade(lesson.questions[0], "b"))
    assert not store.submit(42, attempt_id, retried["id"], "b", Grade(1, "", True))
    assert store.current_run(99, attempt_id, 0) is None
    assert not store.advance(99, attempt_id, retried["id"])
    assert len(_store(tmp_path).attempt_runs(42, attempt_id)) == 2


def test_completed_attempt_mastery_and_module_progress(tmp_path: Path):
    store = _store(tmp_path)
    lesson = store.catalog.lesson("ip_subnets")
    first = store.start_attempt(42, lesson)
    for index, question in enumerate(lesson.questions):
        run = store.ensure_run(42, first, index)
        answer = (question["answer"] if question["kind"] == "choice" else
                  question["accepted"][0] if question["kind"] == "text" else
                  question["parts"][0]["aliases"][0])
        assert store.submit(42, first, run["id"], answer, grade(question, answer))
        assert store.advance(42, first, run["id"])
    result = store.attempt(42, first)
    assert result["completed_at_utc"]
    assert result["total_points"] == 4.5
    assert store.lesson_progress(42, lesson.id)["mastered_at_utc"]
    assert store.lesson_progress(42, lesson.id)["completed_at_utc"] is None
    assert store.progress_snapshot(42)["modules"][0]["mastered_lessons"] == 1
    second = _store(tmp_path).start_attempt(42, lesson)
    assert second != first
    assert len(store.completed_attempts(42, lesson.id)) == 1
    assert store.progress_snapshot(99)["attempts"] == []
