import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyberpath_ai.ai.explainer import (
    AIExplainer, ExplainError, ExplanationStore, FIELDS, content_version,
    parse_response,
)
from cyberpath_ai.ai import check_model as model_check
from cyberpath_ai.config import Settings
from cyberpath_ai.news.models import NewsItem


def item(description: str = "A source-backed description") -> NewsItem:
    return NewsItem("NVD", "Test CVE-2026-1234", description,
                    datetime(2026, 10, 21, tzinfo=UTC),
                    "https://nvd.nist.gov/vuln/detail/CVE-2026-1234",
                    ("CVE-2026-1234",))


BODY = {field: f"Russian {field}" for field in FIELDS if field != "terms"}
BODY["terms"] = [{"term": "CVE", "meaning": "Identifier"},
                 {"term": "patch", "meaning": "Security update"}]


def settings(path: Path, *, key: str = "test-key", cap: int = 20) -> Settings:
    return Settings("telegram-test", frozenset({42}), news_db_path=path,
                    openai_api_key=key, max_ai_calls_per_day=cap)


def client(response: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        models=SimpleNamespace(retrieve=AsyncMock(return_value=SimpleNamespace(id="gpt-6-luna"))),
        responses=SimpleNamespace(create=AsyncMock(return_value=response or SimpleNamespace(
            status="completed", output_text=json.dumps(BODY)))),
    )


def test_response_parsing_rejects_missing_or_incomplete_content() -> None:
    assert parse_response(SimpleNamespace(status="completed", output_text=json.dumps(BODY))) == BODY
    with pytest.raises(ExplainError, match="incomplete"):
        parse_response(SimpleNamespace(status="incomplete", output_text=json.dumps(BODY)))
    with pytest.raises(ExplainError, match="unreadable"):
        parse_response(SimpleNamespace(status="completed", output_text="bad json"))
    with pytest.raises(ExplainError, match="incomplete"):
        parse_response(SimpleNamespace(status="completed", output_text='{"happened":"x"}'))
    with pytest.raises(ExplainError, match="incomplete"):
        parse_response(SimpleNamespace(status="completed", output_text=json.dumps({
            **BODY, "terms": [{"term": "CVE", "meaning": "ID"}],
        })))


def test_missing_key_and_model_preflight_errors_are_clear(tmp_path: Path) -> None:
    async def exercise() -> None:
        missing = AIExplainer(settings(tmp_path / "missing.sqlite", key=""))
        with pytest.raises(ExplainError, match="OPENAI_API_KEY"):
            await missing.explain(item())
        fake = client()
        fake.models.retrieve.side_effect = RuntimeError("private sensitive API response")
        explainer = AIExplainer(settings(tmp_path / "model.sqlite"), client=fake)
        with pytest.raises(ExplainError, match="Could not check model access") as caught:
            await explainer.explain(item())
        assert "private sensitive" not in str(caught.value)
        fake.responses.create.assert_not_awaited()
    asyncio.run(exercise())


@pytest.mark.parametrize("error,message", [
    (type("APITimeoutError", (Exception,), {})(), "timed out"),
    (type("RateLimitError", (Exception,), {})(), "rate limit"),
    (RuntimeError("secret"), "temporarily unavailable"),
])
def test_api_failures_are_safe(tmp_path: Path, error: Exception, message: str) -> None:
    async def exercise() -> None:
        fake = client()
        fake.responses.create.side_effect = error
        explainer = AIExplainer(settings(tmp_path / "errors.sqlite"), client=fake)
        with pytest.raises(ExplainError, match=message) as caught:
            await explainer.explain(item())
        assert "secret" not in str(caught.value)
    asyncio.run(exercise())


def test_cache_levels_content_version_cap_and_request_shape(tmp_path: Path) -> None:
    async def exercise() -> None:
        fake = client()
        path = tmp_path / "ai.sqlite"
        explainer = AIExplainer(settings(path, cap=4), client=fake)
        source = item()
        assert await explainer.explain(source) == BODY
        assert await explainer.explain(source) == BODY
        assert fake.responses.create.await_count == 1
        assert await explainer.explain(source, "intermediate") == BODY
        assert await explainer.explain(source, "advanced") == BODY
        assert fake.responses.create.await_count == 3
        assert fake.models.retrieve.await_count == 1
        args = fake.responses.create.await_args.kwargs
        assert args["model"] == "gpt-6-luna"
        assert args["reasoning"] == {"effort": "none"}
        assert args["tools"] == [] and args["tool_choice"] == "none"
        assert args["store"] is False
        assert "Level: Advanced" in args["input"]
        assert "CVE-2026-1234" in args["input"]
        assert "test-key" not in str(args)
        changed = item("Description changed")
        assert content_version(source) != content_version(changed)
        assert await explainer.explain(changed) == BODY
        assert fake.responses.create.await_count == 4
        with pytest.raises(ExplainError, match="Daily AI request limit"):
            await explainer.explain(item("Description changed again"))
        assert fake.responses.create.await_count == 4
        restarted = AIExplainer(settings(path, key=""), store=ExplanationStore(path))
        assert await restarted.explain(source) == BODY
    asyncio.run(exercise())


def test_concurrent_identical_requests_generate_once(tmp_path: Path) -> None:
    async def exercise() -> None:
        fake = client()
        explainer = AIExplainer(settings(tmp_path / "concurrent.sqlite"), client=fake)
        a, b = await asyncio.gather(explainer.explain(item()), explainer.explain(item()))
        assert a == b == BODY
        fake.responses.create.assert_awaited_once()
    asyncio.run(exercise())


def test_read_only_model_check_never_generates(monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert asyncio.run(model_check.check_model()) == 2
    assert "not set" in capsys.readouterr().out
    monkeypatch.setenv("OPENAI_API_KEY", "mock-key")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    fake = client()

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["api_key"] == "mock-key"
            self.models = fake.models

        async def __aenter__(self) -> "FakeOpenAI":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(model_check, "AsyncOpenAI", FakeOpenAI)
    assert asyncio.run(model_check.check_model()) == 0
    fake.models.retrieve.assert_awaited_once_with("gpt-6-luna")
    fake.responses.create.assert_not_awaited()
    assert "mock-key" not in capsys.readouterr().out
