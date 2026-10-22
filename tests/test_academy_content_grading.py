import copy
import json
from pathlib import Path

import pytest

from cyberpath_ai.academy.content import ContentError, load_catalog
from cyberpath_ai.academy.grading import grade


def test_curated_catalog_has_nine_lessons_and_45_valid_questions():
    catalog = load_catalog()
    assert {module.id for module in catalog.modules} == {"networking", "linux", "web_security"}
    assert all(len(module.lessons) == 3 for module in catalog.modules)
    assert sum(len(lesson.questions) for module in catalog.modules for lesson in module.lessons) == 45
    assert len(catalog.skills) == 18
    assert len(catalog.skill_for_question) == 45
    for module in catalog.modules:
        for lesson in module.lessons:
            assert len(lesson.sections) >= 3
            assert len(lesson.objectives) >= 2
            for question in lesson.questions:
                assert len(question["hints"]) == 3
                if question["kind"] == "choice":
                    assert grade(question, question["answer"]).points == 1
                    assert all(grade(question, option["id"]).points == int(option["id"] == question["answer"])
                               for option in question["options"])
                elif question["kind"] == "text":
                    assert all(grade(question, answer).points == 1 for answer in question["accepted"])
                else:
                    answer = ", ".join(part["aliases"][0] for part in question["parts"])
                    assert grade(question, answer).points == 1


def test_grading_normalization_equivalents_and_partial_credit():
    catalog = load_catalog()
    http = catalog.lesson("dns_https").questions[2]
    assert grade(http, "  http 404! ").points == 1
    assert grade(http, "4040").points == 0
    parts = catalog.lesson("tcp_udp").questions[4]
    assert grade(parts, "retransmission").points == 0.5
    assert grade(parts, "retransmission, random").points == 0.25
    assert grade(parts, "retransmission, ordered delivery").points == 1


@pytest.mark.parametrize("mutation", ["bad_answer", "two_hints", "bad_points", "duplicate_id"])
def test_rejects_malformed_lesson_json(tmp_path, mutation):
    source = json.loads((Path(__file__).parents[1] /
                         "cyberpath_ai/academy/lessons/networking.json").read_text(encoding="utf-8"))
    content = copy.deepcopy(source)
    questions = content["lessons"][0]["questions"]
    if mutation == "bad_answer":
        questions[0]["answer"] = "not_an_option"
    elif mutation == "two_hints":
        questions[0]["hints"] = questions[0]["hints"][:2]
    elif mutation == "bad_points":
        questions[4]["parts"][0]["points"] = 0.2
    else:
        questions[1]["id"] = questions[0]["id"]
    (tmp_path / "networking.json").write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ContentError):
        load_catalog(tmp_path)


def test_rejects_unmapped_or_repeated_skill_tag(tmp_path):
    packaged = Path(__file__).parents[1] / "cyberpath_ai/academy/skills.json"
    raw = json.loads(packaged.read_text(encoding="utf-8"))
    raw["skills"][0]["question_ids"].remove("ip_q1")
    target = tmp_path / "skills.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ContentError, match="Every question"):
        load_catalog(skills_path=target)
    raw["skills"][0]["question_ids"].append("ip_q1")
    raw["skills"][1]["question_ids"].append("ip_q1")
    target.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ContentError, match="Invalid or repeated"):
        load_catalog(skills_path=target)
