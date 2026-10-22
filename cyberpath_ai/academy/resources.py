"""Validated, offline catalog of source-checked learning resources."""

from dataclasses import dataclass
from datetime import date
from importlib.resources import files
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from cyberpath_ai.academy.content import Catalog


class ResourceError(ValueError):
    """A curated resource or internal reference is invalid."""


@dataclass(frozen=True)
class Resource:
    id: str
    title: str
    provider: str
    category: str
    url: str
    description_ru: str
    lesson_ids: tuple[str, ...]
    skill_ids: tuple[str, ...]
    difficulty: str
    language: str
    access: str
    duration_minutes: int | None
    prerequisites: tuple[str, ...]
    objectives: tuple[str, ...]
    why: str
    verified_at: date
    path: bool = False
    prerequisite_lesson_ids: tuple[str, ...] = ()


class ResourceCatalog:
    def __init__(self, resources: tuple[Resource, ...]):
        self.resources = resources
        self.by_id = {resource.id: resource for resource in resources}


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResourceError(f"Missing resource field: {field}")
    return value.strip()


def _ids(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ResourceError(f"Expected nonempty list: {field}")
    result = tuple(_text(item, field) for item in value)
    if len(set(result)) != len(result):
        raise ResourceError(f"Duplicate mapping: {field}")
    return result


def load_resources(catalog: Catalog, path: Path | None = None) -> ResourceCatalog:
    path = path or Path(str(files("cyberpath_ai.academy").joinpath("resources.json")))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ResourceError("Invalid resources.json") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("resources"), list):
        raise ResourceError("Expected resources list")
    resources: list[Resource] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for entry in raw["resources"]:
        if not isinstance(entry, dict):
            raise ResourceError("Invalid resource entry")
        resource_id = _text(entry.get("id"), "id")
        if not re.fullmatch(r"[a-z0-9_]{1,40}", resource_id) or resource_id in seen_ids:
            raise ResourceError(f"Invalid or duplicate resource ID: {resource_id}")
        seen_ids.add(resource_id)
        url = _text(entry.get("url"), "url")
        try:
            parsed = urlsplit(url)
        except ValueError as error:
            raise ResourceError(f"Invalid resource URL: {resource_id}") from error
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or any(character.isspace() for character in url) or url in seen_urls):
            raise ResourceError(f"Invalid or duplicate resource URL: {resource_id}")
        seen_urls.add(url)
        lessons = _ids(entry.get("lesson_ids"), "lesson_ids")
        skills = _ids(entry.get("skill_ids"), "skill_ids")
        if any(item not in catalog.lesson_by_id for item in lessons):
            raise ResourceError(f"Unknown lesson mapping: {resource_id}")
        if any(item not in catalog.skill_by_id for item in skills):
            raise ResourceError(f"Unknown skill mapping: {resource_id}")
        if any(catalog.skill_by_id[item].lesson_id not in lessons for item in skills):
            raise ResourceError(f"Skill outside mapped lessons: {resource_id}")
        category = _text(entry.get("category"), "category")
        difficulty = _text(entry.get("difficulty"), "difficulty")
        access = _text(entry.get("access"), "access")
        if category not in {"video", "labs", "reading", "practice"}:
            raise ResourceError(f"Unknown category: {resource_id}")
        if difficulty not in {"Beginner", "Intermediate", "Advanced"}:
            raise ResourceError(f"Unknown difficulty: {resource_id}")
        if access not in {"Free", "Paid", "Unknown"}:
            raise ResourceError(f"Unknown access status: {resource_id}")
        duration = entry.get("duration_minutes")
        if duration is not None and (not isinstance(duration, int) or isinstance(duration, bool) or duration < 1):
            raise ResourceError(f"Invalid duration: {resource_id}")
        prerequisites = entry.get("prerequisites")
        if not isinstance(prerequisites, list) or any(
                not isinstance(item, str) or not item.strip() for item in prerequisites):
            raise ResourceError(f"Invalid prerequisites: {resource_id}")
        objectives = _ids(entry.get("objectives"), "objectives")
        try:
            verified = date.fromisoformat(_text(entry.get("verified_at"), "verified_at"))
        except ValueError as error:
            raise ResourceError(f"Invalid verification date: {resource_id}") from error
        if verified > date.today():
            raise ResourceError(f"Future verification date: {resource_id}")
        if not isinstance(entry.get("path", False), bool):
            raise ResourceError(f"Invalid path flag: {resource_id}")
        prerequisite_ids = entry.get("prerequisite_lesson_ids", [])
        if not isinstance(prerequisite_ids, list) or any(
                not isinstance(item, str) or item not in catalog.lesson_by_id
                for item in prerequisite_ids) or len(set(prerequisite_ids)) != len(prerequisite_ids):
            raise ResourceError(f"Invalid prerequisite lesson: {resource_id}")
        resources.append(Resource(resource_id, _text(entry.get("title"), "title"),
                                  _text(entry.get("provider"), "provider"), category, url,
                                  _text(entry.get("description_ru"), "description_ru"),
                                  lessons, skills, difficulty,
                                  _text(entry.get("language"), "language"), access,
                                  duration, tuple(prerequisites), objectives,
                                  _text(entry.get("why"), "why"), verified,
                                  entry.get("path", False), tuple(prerequisite_ids)))
    return ResourceCatalog(tuple(resources))
