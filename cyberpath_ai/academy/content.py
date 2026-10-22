"""Load and validate curated, packaged JSON lessons."""

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
import re

from cyberpath_ai.academy.grading import normalize


class ContentError(ValueError):
    """A curated lesson or answer key is malformed."""


@dataclass(frozen=True)
class Lesson:
    id: str
    module_id: str
    title: str
    objectives: tuple[str, ...]
    sections: tuple[dict, ...]
    summary: str
    questions: tuple[dict, ...]


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    description: str
    lessons: tuple[Lesson, ...]


@dataclass(frozen=True)
class Skill:
    id: str
    title: str
    lesson_id: str
    module_id: str
    question_ids: tuple[str, ...]


class Catalog:
    def __init__(self, modules: tuple[Module, ...], skills: tuple[Skill, ...] = ()) -> None:
        self.modules = modules
        self.module_by_id = {module.id: module for module in modules}
        self.lesson_by_id = {lesson.id: lesson for module in modules for lesson in module.lessons}
        self.question_by_id = {question["id"]: (lesson, index, question)
                               for module in modules for lesson in module.lessons
                               for index, question in enumerate(lesson.questions)}
        self.skills = skills
        self.skill_by_id = {skill.id: skill for skill in skills}
        self.skill_for_question = {question_id: skill for skill in skills
                                   for question_id in skill.question_ids}

    def lesson(self, lesson_id: str) -> Lesson | None:
        return self.lesson_by_id.get(lesson_id)


def _string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentError(f"Missing text: {where}")
    return value.strip()


def _slug(value: object, where: str) -> str:
    text = _string(value, where)
    if not re.fullmatch(r"[a-z0-9_]{1,40}", text):
        raise ContentError(f"Invalid ID: {where}")
    return text


def _strings(value: object, where: str, minimum: int = 1) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ContentError(f"Expected text list: {where}")
    return tuple(_string(entry, where) for entry in value)


def _question(raw: object, where: str) -> dict:
    if not isinstance(raw, dict):
        raise ContentError(f"Expected question object: {where}")
    question = dict(raw)
    _slug(question.get("id"), where + ".id")
    _string(question.get("prompt"), where + ".prompt")
    _string(question.get("explanation"), where + ".explanation")
    hints = _strings(question.get("hints"), where + ".hints", 3)
    if len(hints) != 3 or len({normalize(hint) for hint in hints}) != 3:
        raise ContentError(f"Three distinct progressive hints required: {where}")
    kind = question.get("kind")
    if kind == "choice":
        options = question.get("options")
        if not isinstance(options, list) or not 3 <= len(options) <= 5:
            raise ContentError(f"Choice options required: {where}")
        ids = set()
        for option in options:
            if not isinstance(option, dict):
                raise ContentError(f"Invalid option: {where}")
            option_id = _slug(option.get("id"), where + ".option")
            ids.add(option_id)
            _string(option.get("text"), where + ".option.text")
            _string(option.get("feedback"), where + ".option.feedback")
        if len(ids) != len(options) or question.get("answer") not in ids:
            raise ContentError(f"Invalid choice answer key: {where}")
    elif kind == "text":
        _string(question.get("wrong_feedback"), where + ".wrong_feedback")
        accepted = _strings(question.get("accepted"), where + ".accepted")
        if len({normalize(value) for value in accepted}) != len(accepted):
            raise ContentError(f"Duplicate accepted answer: {where}")
    elif kind == "parts":
        _string(question.get("wrong_feedback"), where + ".wrong_feedback")
        parts = question.get("parts")
        if not isinstance(parts, list) or not 2 <= len(parts) <= 3:
            raise ContentError(f"Two or three scoring parts required: {where}")
        total = 0.0
        aliases: set[str] = set()
        for part in parts:
            if not isinstance(part, dict):
                raise ContentError(f"Invalid scoring part: {where}")
            _string(part.get("name"), where + ".part.name")
            choices = _strings(part.get("aliases"), where + ".part.aliases")
            normalized = {normalize(choice) for choice in choices}
            if len(normalized) != len(choices) or aliases & normalized:
                raise ContentError(f"Overlapping scoring aliases: {where}")
            aliases.update(normalized)
            points = part.get("points")
            if not isinstance(points, (int, float)) or isinstance(points, bool) or points <= 0:
                raise ContentError(f"Invalid scoring points: {where}")
            total += points
        if abs(total - 1.0) > 0.0001:
            raise ContentError(f"Scoring points must total 1: {where}")
        penalty = question.get("wrong_penalty")
        if not isinstance(penalty, (int, float)) or not 0 <= penalty <= 1:
            raise ContentError(f"Invalid wrong answer penalty: {where}")
    else:
        raise ContentError(f"Unknown question kind: {where}")
    return question


def load_catalog(directory: Path | None = None, skills_path: Path | None = None) -> Catalog:
    path = directory or Path(str(files("cyberpath_ai.academy.lessons")))
    modules = []
    module_ids: set[str] = set()
    lesson_ids: set[str] = set()
    question_ids: set[str] = set()
    order = {"networking": 0, "linux": 1, "web_security": 2}
    for file in sorted(path.glob("*.json"),
                       key=lambda entry: (order.get(entry.stem, 100), entry.name)):
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ContentError(f"Invalid lesson file: {file.name}") from error
        if not isinstance(raw, dict):
            raise ContentError(f"Module must be an object: {file.name}")
        module_id = _slug(raw.get("id"), file.name + ".id")
        if module_id in module_ids:
            raise ContentError(f"Duplicate module ID: {module_id}")
        module_ids.add(module_id)
        title = _string(raw.get("title"), module_id + ".title")
        description = _string(raw.get("description"), module_id + ".description")
        lessons_raw = raw.get("lessons")
        if not isinstance(lessons_raw, list) or not lessons_raw:
            raise ContentError(f"No lessons: {module_id}")
        lessons = []
        for lesson_raw in lessons_raw:
            if not isinstance(lesson_raw, dict):
                raise ContentError(f"Invalid lesson: {module_id}")
            lesson_id = _slug(lesson_raw.get("id"), module_id + ".lesson.id")
            if lesson_id in lesson_ids:
                raise ContentError(f"Duplicate lesson ID: {lesson_id}")
            lesson_ids.add(lesson_id)
            objectives = _strings(lesson_raw.get("objectives"), lesson_id + ".objectives", 2)
            sections_raw = lesson_raw.get("sections")
            if not isinstance(sections_raw, list) or len(sections_raw) < 3:
                raise ContentError(f"At least three sections required: {lesson_id}")
            for section in sections_raw:
                if not isinstance(section, dict):
                    raise ContentError(f"Invalid section: {lesson_id}")
                for field in ("title", "text", "example"):
                    _string(section.get(field), lesson_id + "." + field)
                _strings(section.get("concepts"), lesson_id + ".concepts")
            questions_raw = lesson_raw.get("questions")
            if not isinstance(questions_raw, list) or len(questions_raw) != 5:
                raise ContentError(f"Exactly five questions required: {lesson_id}")
            questions = tuple(_question(question, lesson_id) for question in questions_raw)
            if len({question["id"] for question in questions}) != 5:
                raise ContentError(f"Duplicate question ID: {lesson_id}")
            for question in questions:
                if question["id"] in question_ids:
                    raise ContentError(f"Duplicate question ID: {question['id']}")
                question_ids.add(question["id"])
            lessons.append(Lesson(lesson_id, module_id,
                                  _string(lesson_raw.get("title"), lesson_id + ".title"),
                                  objectives, tuple(sections_raw),
                                  _string(lesson_raw.get("summary"), lesson_id + ".summary"),
                                  questions))
        modules.append(Module(module_id, title, description, tuple(lessons)))
    if not modules:
        raise ContentError("No module JSON files found")
    skills_file = skills_path or Path(str(files("cyberpath_ai.academy").joinpath("skills.json")))
    try:
        raw_skills = json.loads(skills_file.read_text(encoding="utf-8"))["skills"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ContentError("Invalid skills.json") from error
    if not isinstance(raw_skills, list):
        raise ContentError("skills.json must contain a skills list")
    lessons = {lesson.id: lesson for module in modules for lesson in module.lessons}
    assigned: set[str] = set()
    skill_ids: set[str] = set()
    skills = []
    for raw in raw_skills:
        if not isinstance(raw, dict):
            raise ContentError("Invalid skill entry")
        skill_id = _slug(raw.get("id"), "skill.id")
        if skill_id in skill_ids:
            raise ContentError(f"Duplicate skill ID: {skill_id}")
        skill_ids.add(skill_id)
        lesson_id = _slug(raw.get("lesson_id"), skill_id + ".lesson_id")
        lesson = lessons.get(lesson_id)
        if lesson is None:
            raise ContentError(f"Unknown skill lesson: {lesson_id}")
        tagged = tuple(_slug(value, skill_id + ".question_ids") for value in
                       _strings(raw.get("question_ids"), skill_id + ".question_ids"))
        local_ids = {question["id"] for question in lesson.questions}
        if any(value not in local_ids or value in assigned for value in tagged):
            raise ContentError(f"Invalid or repeated skill question: {skill_id}")
        assigned.update(tagged)
        skills.append(Skill(skill_id, _string(raw.get("title"), skill_id + ".title"),
                            lesson_id, lesson.module_id, tagged))
    if assigned != question_ids:
        raise ContentError("Every question must have exactly one skill tag")
    return Catalog(tuple(modules), tuple(skills))
