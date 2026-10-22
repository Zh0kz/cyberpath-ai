"""Deterministic quiz grading; no AI or fuzzy substring matching."""

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class Grade:
    points: float
    feedback: str
    correct: bool


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = re.sub(r"[^\w+./-]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def grade(question: dict, answer: str) -> Grade:
    kind = question["kind"]
    if kind == "choice":
        selected = next((option for option in question["options"] if option["id"] == answer), None)
        if selected is None:
            return Grade(0.0, "Неизвестный вариант ответа.", False)
        correct = answer == question["answer"]
        return Grade(1.0 if correct else 0.0,
                     selected["feedback"] + " " + question["explanation"], correct)
    if kind == "text":
        correct = normalize(answer) in {normalize(value) for value in question["accepted"]}
        return Grade(1.0 if correct else 0.0,
                     ("Верно. " if correct else question["wrong_feedback"] + " ")
                     + question["explanation"], correct)
    if kind == "parts":
        # Explicitly delimited terms are matched as whole normalized answers.
        # Extra, unrecognized entries incur the JSON-defined penalty.
        entries = [normalize(value) for value in re.split(r"[,;\n]", answer) if normalize(value)]
        recognized: set[str] = set()
        earned = 0.0
        found = []
        missing = []
        for part in question["parts"]:
            aliases = {normalize(alias) for alias in part["aliases"]}
            matched = [entry for entry in entries if entry in aliases]
            if matched:
                earned += part["points"]
                recognized.update(matched)
                found.append(part["name"])
            else:
                missing.append(part["name"])
        extras = [entry for entry in entries if entry not in recognized]
        points = round(max(0.0, min(1.0, earned - len(extras) * question["wrong_penalty"])), 2)
        details = []
        if found:
            details.append("Засчитано: " + ", ".join(found) + ".")
        if missing:
            details.append("Не указано: " + ", ".join(missing) + ".")
        if extras:
            details.append("Лишние или нераспознанные элементы снижают балл.")
        if points < 1:
            details.append(question["wrong_feedback"])
        details.append(question["explanation"])
        return Grade(points, " ".join(details), points == 1.0)
    raise ValueError("Unsupported question kind")
