"""Offline regressions for routed small-model output and safe diagnostics."""

import asyncio
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyberpath_ai.ai.explainer import AIExplainer, ExplainError, parse_chat_response
from cyberpath_ai.config import Settings
from cyberpath_ai.news.models import NewsItem


ITEM = NewsItem("NVD", "CVE-2026-1234 in Widget", "A flaw was reported.",
                datetime(2026, 10, 21, tzinfo=UTC),
                "https://nvd.nist.gov/vuln/detail/CVE-2026-1234", ("CVE-2026-1234",))
BODY = {
    "happened": "Подтверждено источником: reported flaw in Widget.",
    "danger": "General impact.", "mechanism": "General mechanism.",
    "example": "Гипотетический пример: a fictional user checks updates.",
    "defense": "Check vendor advisories.",
    "terms": [{"term": "patch", "meaning": "обновление"},
              {"term": "CVE", "meaning": "идентификатор"}],
    "question": "What should you verify?",
}
PLAIN = """[WHAT_HAPPENED]
Подтверждено источником: reported flaw in Widget.
[WHY_IMPORTANT]
General impact.
[HOW_IT_WORKS]
General mechanism.
[HOW_TO_PROTECT]
Check vendor advisories.
[TERMS]
patch — обновление
CVE — идентификатор
[CHECK_QUESTION]
What should you verify?"""


def response(content: str | None, *, finish: str | None = "stop",
             model: str = "liquid/lfm-2.5-2.6b:free") -> SimpleNamespace:
    return SimpleNamespace(model=model, choices=[SimpleNamespace(
        finish_reason=finish, message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=123, completion_tokens=84, total_tokens=207))


def fake_client(result: object) -> SimpleNamespace:
    return SimpleNamespace(get=AsyncMock(return_value={"data": {"label": "valid"}}),
                           chat=SimpleNamespace(completions=SimpleNamespace(
                               create=AsyncMock(return_value=result))))


def settings(path: Path, cap: int = 10) -> Settings:
    return Settings("telegram-test", frozenset({42}), news_db_path=path,
                    ai_provider="openrouter", openrouter_api_key="secret-test-key",
                    max_ai_calls_per_day=cap)


def test_valid_json_markdown_json_and_delimited_plain_text() -> None:
    assert parse_chat_response(response(json.dumps(BODY))) == BODY
    assert parse_chat_response(response("```json\n" + json.dumps(BODY) + "\n```")) == BODY
    parsed = parse_chat_response(response(PLAIN))
    assert parsed["happened"] == BODY["happened"]
    assert parsed["terms"] == BODY["terms"]
    assert parsed["example"] == ""


@pytest.mark.parametrize("raw,finish,category", [
    ("", "stop", "empty_content"),
    (PLAIN, "length", "truncated"),
    ("[WHAT_HAPPENED]\nOnly one section", "stop", "missing_sections"),
    (PLAIN.replace("[WHY_IMPORTANT]\nGeneral impact.\n", ""), "stop", "missing_sections"),
    (PLAIN.replace("Подтверждено источником:", ""), "stop", "unlabeled_facts"),
])
def test_incomplete_outputs_are_rejected(raw: str, finish: str, category: str) -> None:
    with pytest.raises(ExplainError) as caught:
        parse_chat_response(response(raw, finish=finish))
    assert caught.value.category == category


def test_advanced_plain_text_requires_hypothetical_example() -> None:
    advanced = PLAIN.replace("[HOW_TO_PROTECT]", "[EXAMPLE]\nГипотетический пример: a fictional case.\n[HOW_TO_PROTECT]")
    assert parse_chat_response(response(advanced), level="advanced")["example"].startswith(
        "Гипотетический пример:")
    with pytest.raises(ExplainError, match="incomplete"):
        parse_chat_response(response(PLAIN), level="advanced")


def test_generation_metadata_cache_and_daily_quota(tmp_path: Path,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        fake = fake_client(response(PLAIN))
        explainer = AIExplainer(settings(tmp_path / "ai.sqlite", cap=1), client=fake)
        first = await explainer.explain(ITEM)
        assert first["question"] == BODY["question"]
        assert await explainer.explain(ITEM) == first
        fake.chat.completions.create.assert_awaited_once()
        request = fake.chat.completions.create.await_args.kwargs
        assert request["model"] == "openrouter/free"
        assert "response_format" not in request
        assert "[WHAT_HAPPENED]" in request["messages"][0]["content"]
        with pytest.raises(ExplainError, match="Daily AI request limit"):
            await explainer.explain(NewsItem(
                ITEM.source, ITEM.title, "Changed description", ITEM.published_at,
                ITEM.source_url, ITEM.cve_ids))
        fake.chat.completions.create.assert_awaited_once()

    with caplog.at_level(logging.INFO, logger="cyberpath_ai.ai"):
        asyncio.run(exercise())
    records = [json.loads(record.message) for record in caplog.records
               if record.name == "cyberpath_ai.ai"]
    assert len(records) == 1
    record = records[0]
    assert record["provider"] == "openrouter"
    assert record["configured_model"] == "openrouter/free"
    assert record["actual_model"] == "liquid/lfm-2.5-2.6b:free"
    assert record["finish_reason"] == "stop" and record["content_length"] == len(PLAIN)
    assert record["completion_tokens"] == 84 and record["outcome"] == "success"
    assert "secret-test-key" not in caplog.text and PLAIN not in caplog.text


def test_truncated_response_diagnostic_is_safe(tmp_path: Path,
                                               caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        fake = fake_client(response(PLAIN[:45], finish="length"))
        with pytest.raises(ExplainError, match="token limit"):
            await AIExplainer(settings(tmp_path / "ai.sqlite"), client=fake).explain(ITEM)

    with caplog.at_level(logging.INFO, logger="cyberpath_ai.ai"):
        asyncio.run(exercise())
    result = json.loads(caplog.records[-1].message)
    assert result["phase"] == "generation" and result["failure_category"] == "truncated"
    assert result["truncated"] is True
    assert "secret-test-key" not in caplog.text and PLAIN[:45] not in caplog.text


@pytest.mark.parametrize("content,category,phase", [
    ("not JSON or marked text", "json_parse", "parsing"),
    (json.dumps({"happened": "only one field"}), "missing_sections", "validation"),
])
def test_parse_and_validation_diagnostics(tmp_path: Path, caplog: pytest.LogCaptureFixture,
                                          content: str, category: str, phase: str) -> None:
    async def exercise() -> None:
        fake = fake_client(response(content))
        with pytest.raises(ExplainError):
            await AIExplainer(settings(tmp_path / "ai.sqlite"), client=fake).explain(ITEM)

    with caplog.at_level(logging.INFO, logger="cyberpath_ai.ai"):
        asyncio.run(exercise())
    result = json.loads(caplog.records[-1].message)
    assert result["failure_category"] == category and result["phase"] == phase
    assert result["content_length"] == len(content) and not result["truncated"]
    assert content not in caplog.text


def test_unsupported_response_format_is_classified_without_retry(tmp_path: Path,
                                                                 caplog: pytest.LogCaptureFixture) -> None:
    async def exercise() -> None:
        fake = fake_client(response(PLAIN))
        fake.chat.completions.create.side_effect = type("BadRequestError", (Exception,), {})()
        with pytest.raises(ExplainError, match="request format"):
            await AIExplainer(settings(tmp_path / "ai.sqlite"), client=fake).explain(ITEM)
        fake.chat.completions.create.assert_awaited_once()

    with caplog.at_level(logging.INFO, logger="cyberpath_ai.ai"):
        asyncio.run(exercise())
    assert json.loads(caplog.records[-1].message)["failure_category"] == "compatibility"


def test_unknown_cve_is_not_cached(tmp_path: Path) -> None:
    async def exercise() -> None:
        fake = fake_client(response(json.dumps({**BODY,
            "happened": "Подтверждено источником: CVE-2026-9999 affects Widget."})))
        with pytest.raises(ExplainError, match="CVE absent"):
            await AIExplainer(settings(tmp_path / "ai.sqlite"), client=fake).explain(ITEM)
        assert fake.chat.completions.create.await_count == 1

    asyncio.run(exercise())
