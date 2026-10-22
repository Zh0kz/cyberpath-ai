"""Validated, fictional investigations and deterministic offline answer checks."""

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
import re

from cyberpath_ai.academy.content import Catalog
from cyberpath_ai.academy.grading import Grade, normalize
from cyberpath_ai.academy.resources import ResourceCatalog


class InvestigationError(ValueError):
    """An investigation or its answer key is malformed."""


@dataclass(frozen=True)
class Step:
    id: str
    purpose: str
    evidence: str
    prompt: str
    kind: str
    answer: str
    options: tuple[dict, ...]
    accepted: tuple[str, ...]
    parts: tuple[dict, ...]
    answer_display: str
    explanation: str
    wrong_feedback: str
    hints: tuple[str, str, str]


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    description: str
    difficulty: str
    prerequisites: tuple[str, ...]
    objectives: tuple[str, ...]
    lesson_id: str
    skill_ids: tuple[str, ...]
    resource_ids: tuple[str, ...]
    debrief: str
    steps: tuple[Step, ...]


class InvestigationCatalog:
    def __init__(self, scenarios: tuple[Scenario, ...]):
        self.scenarios = scenarios
        self.by_id = {scenario.id: scenario for scenario in scenarios}


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvestigationError(f"Missing text: {where}")
    return value.strip()


def _id(value: object, where: str) -> str:
    value = _text(value, where)
    if not re.fullmatch(r"[a-z0-9_]{1,40}", value):
        raise InvestigationError(f"Invalid ID: {where}")
    return value


def _texts(value: object, where: str, minimum: int = 1) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) < minimum:
        raise InvestigationError(f"Expected text list: {where}")
    return tuple(_text(item, where) for item in value)


def _ids(value: object, where: str) -> tuple[str, ...]:
    values = _texts(value, where)
    result = tuple(_id(item, where) for item in values)
    if len(result) != len(set(result)):
        raise InvestigationError(f"Repeated ID: {where}")
    return result


def load_investigations(catalog: Catalog, resources: ResourceCatalog,
                        path: Path | None = None) -> InvestigationCatalog:
    path = path or Path(str(files("cyberpath_ai.academy").joinpath("investigations.json")))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InvestigationError("Invalid investigations.json") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("scenarios"), list) or not raw["scenarios"]:
        raise InvestigationError("Expected nonempty scenarios list")
    scenarios = []
    seen_scenarios: set[str] = set()
    seen_steps: set[str] = set()
    for entry in raw["scenarios"]:
        if not isinstance(entry, dict):
            raise InvestigationError("Invalid scenario")
        scenario_id = _id(entry.get("id"), "scenario.id")
        if scenario_id in seen_scenarios:
            raise InvestigationError(f"Repeated scenario: {scenario_id}")
        seen_scenarios.add(scenario_id)
        lesson_id = _id(entry.get("lesson_id"), scenario_id + ".lesson_id")
        if lesson_id not in catalog.lesson_by_id:
            raise InvestigationError(f"Unknown lesson: {scenario_id}")
        skills = _ids(entry.get("skill_ids"), scenario_id + ".skill_ids")
        if any(skill not in catalog.skill_by_id or
               catalog.skill_by_id[skill].lesson_id != lesson_id for skill in skills):
            raise InvestigationError(f"Invalid skill mapping: {scenario_id}")
        resource_ids = _ids(entry.get("resource_ids"), scenario_id + ".resource_ids")
        if any(resource not in resources.by_id or
               lesson_id not in resources.by_id[resource].lesson_ids or
               not set(skills).intersection(resources.by_id[resource].skill_ids)
               for resource in resource_ids):
            raise InvestigationError(f"Invalid resource mapping: {scenario_id}")
        difficulty = _text(entry.get("difficulty"), scenario_id + ".difficulty")
        if difficulty not in {"Beginner", "Intermediate", "Advanced"}:
            raise InvestigationError(f"Invalid difficulty: {scenario_id}")
        steps_raw = entry.get("steps")
        if not isinstance(steps_raw, list) or not 4 <= len(steps_raw) <= 6:
            raise InvestigationError(f"Expected 4–6 steps: {scenario_id}")
        steps = []
        for item in steps_raw:
            if not isinstance(item, dict):
                raise InvestigationError(f"Invalid step: {scenario_id}")
            step_id = _id(item.get("id"), scenario_id + ".step.id")
            if step_id in seen_steps:
                raise InvestigationError(f"Repeated step: {step_id}")
            seen_steps.add(step_id)
            hints = _texts(item.get("hints"), step_id + ".hints", 3)
            if len(hints) != 3 or len({normalize(hint) for hint in hints}) != 3:
                raise InvestigationError(f"Three unique hints required: {step_id}")
            kind = item.get("kind")
            options: tuple[dict, ...] = ()
            accepted: tuple[str, ...] = ()
            parts: tuple[dict, ...] = ()
            if kind == "choice":
                raw_options = item.get("options")
                if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= 4:
                    raise InvestigationError(f"Invalid choices: {step_id}")
                options = tuple(raw_options)
                if any(not isinstance(option, dict) or
                       not all(option.get(field) for field in ("id", "text", "feedback"))
                       for option in options):
                    raise InvestigationError(f"Invalid option: {step_id}")
                ids = [_id(option["id"], step_id + ".option.id") for option in options]
                if len(set(ids)) != len(ids) or item.get("answer") not in ids:
                    raise InvestigationError(f"Invalid answer key: {step_id}")
                for option in options:
                    _text(option["text"], step_id + ".option.text")
                    _text(option["feedback"], step_id + ".option.feedback")
            elif kind == "text":
                accepted = _texts(item.get("accepted"), step_id + ".accepted")
                if len({normalize(value) for value in accepted}) != len(accepted):
                    raise InvestigationError(f"Repeated accepted answer: {step_id}")
            elif kind == "parts":
                raw_parts = item.get("parts")
                if not isinstance(raw_parts, list) or len(raw_parts) != 2:
                    raise InvestigationError(f"Exactly two evidence parts required: {step_id}")
                parts = tuple(raw_parts)
                aliases: set[str] = set()
                for part in parts:
                    if not isinstance(part, dict):
                        raise InvestigationError(f"Invalid evidence part: {step_id}")
                    _text(part.get("name"), step_id + ".part.name")
                    values = _texts(part.get("aliases"), step_id + ".part.aliases")
                    normalized = {normalize(value) for value in values}
                    if len(normalized) != len(values) or normalized & aliases:
                        raise InvestigationError(f"Overlapping part aliases: {step_id}")
                    aliases.update(normalized)
            else:
                raise InvestigationError(f"Invalid step kind: {step_id}")
            display = _text(item.get("answer_display"), step_id + ".answer_display")
            if kind == "choice" and display != next(option["text"] for option in options
                                                     if option["id"] == item["answer"]):
                raise InvestigationError(f"Choice display disagrees with key: {step_id}")
            step = Step(step_id, _text(item.get("purpose"), step_id + ".purpose"),
                        _text(item.get("evidence"), step_id + ".evidence"),
                        _text(item.get("prompt"), step_id + ".prompt"), kind,
                        item.get("answer", ""), options, accepted, parts, display,
                        _text(item.get("explanation"), step_id + ".explanation"),
                        _text(item.get("wrong_feedback"), step_id + ".wrong_feedback"),
                        (hints[0], hints[1], hints[2]))
            if kind != "choice" and grade_step(step, display).points != 1:
                raise InvestigationError(f"Displayed answer disagrees with key: {step_id}")
            steps.append(step)
        if sum(step.kind != "choice" for step in steps) < 3:
            raise InvestigationError(f"Investigation needs evidence-based text steps: {scenario_id}")
        scenarios.append(Scenario(scenario_id, _text(entry.get("title"), scenario_id + ".title"),
                                  _text(entry.get("description"), scenario_id + ".description"),
                                  difficulty, _texts(entry.get("prerequisites"), scenario_id + ".prerequisites"),
                                  _texts(entry.get("objectives"), scenario_id + ".objectives", 2),
                                  lesson_id, skills, resource_ids,
                                  _text(entry.get("debrief"), scenario_id + ".debrief"), tuple(steps)))
    return InvestigationCatalog(tuple(scenarios))


def grade_step(step: Step, answer: str) -> Grade:
    """Exact curated equivalents only; no substring or model-based grading."""
    if step.kind == "choice":
        option = next((item for item in step.options if item["id"] == answer), None)
        if option is None:
            return Grade(0, "Неизвестный вариант.", False)
        correct = answer == step.answer
        return Grade(1 if correct else 0, option["feedback"], correct)
    if step.kind == "text":
        correct = normalize(answer) in {normalize(item) for item in step.accepted}
        return Grade(1 if correct else 0,
                     "Верно: вы связали ответ с уликой." if correct else step.wrong_feedback,
                     correct)
    if step.kind == "parts":
        entries = [normalize(item) for item in re.split(r"[,;\n]", answer) if normalize(item)]
        matched: set[str] = set()
        for part in step.parts:
            if any(item in {normalize(alias) for alias in part["aliases"]} for item in entries):
                matched.add(part["name"])
        if any(item not in {normalize(alias) for part in step.parts for alias in part["aliases"]}
               for item in entries):
            return Grade(0, step.wrong_feedback, False)
        points = len(matched) / 2
        feedback = ("Засчитано: " + ", ".join(sorted(matched)) + ". " if matched else "")
        if points < 1:
            feedback += step.wrong_feedback
        else:
            feedback += "Обе части подтверждены уликами."
        return Grade(points, feedback, points == 1)
    raise ValueError("Unsupported investigation step")
