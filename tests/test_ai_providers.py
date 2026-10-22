import asyncio
from datetime import UTC, datetime
import json
import httpx
from openai import AsyncOpenAI
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cyberpath_ai.ai import check_model as model_check
from cyberpath_ai.ai.explainer import AIExplainer, ExplainError, parse_chat_response
from cyberpath_ai.ai.explainer import ExplanationStore
from cyberpath_ai.ai.providers import (
    OPENROUTER_BASE_URL, ProviderAdapter, ProviderSelection,
    classify_provider_error,
)
from cyberpath_ai.config import Settings
from cyberpath_ai.news.models import NewsItem


ARTICLE = NewsItem(
    "CISA KEV", "Source title", "Source description",
    datetime(2026, 10, 21, tzinfo=UTC), "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
)
BODY = {
    "happened": "Подтверждено источником: событие.",
    "danger": "Общая опасность.", "mechanism": "Общий механизм.",
    "example": "Гипотетический пример: ситуация.",
    "defense": "Практическая защита.",
    "terms": [{"term": "patch", "meaning": "обновление"},
              {"term": "CVE", "meaning": "идентификатор"}],
    "question": "Что следует проверить?",
}


def chat_response(content: str | None = None, finish: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish,
        message=SimpleNamespace(content=content if content is not None else json.dumps(BODY)),
    )])


def router_client(*, price: str = "0", response: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        get=AsyncMock(return_value={"data": {"label": "test key"}}),
        chat=SimpleNamespace(completions=SimpleNamespace(
            create=AsyncMock(return_value=response or chat_response()))),
        responses=SimpleNamespace(create=AsyncMock()),
    )


def settings(path: Path, *, provider: str = "openrouter", router_key: str = "router-test",
             openai_key: str = "", router_model: str = "openrouter/free") -> Settings:
    return Settings("telegram-test", frozenset({42}), news_db_path=path,
                    ai_provider=provider, openrouter_api_key=router_key,
                    openai_api_key=openai_key, openrouter_model=router_model)


def test_environment_selects_openrouter_free_with_ten_requests() -> None:
    config = Settings.from_env({
        "TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": "42",
        "OPENROUTER_API_KEY": "private-router-key",
    })
    assert config.ai_provider == "openrouter"
    assert config.openrouter_model == "openrouter/free"
    assert config.max_ai_calls_per_day == 10
    assert Settings.from_env({
        "TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": "42",
        "AI_PROVIDER": "openrouter", "OPENAI_MODEL": "",
        "OPENROUTER_API_KEY": "router-only-key",
    }).openai_model == "gpt-6-luna"
    assert "private-router-key" not in repr(config)
    selection = ProviderSelection.from_settings(config)
    assert selection.cache_key == "openrouter:openrouter/free"
    assert "private-router-key" not in repr(selection)
    openai_config = Settings.from_env({
        "TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": "42",
        "AI_PROVIDER": "openai", "OPENAI_API_KEY": "openai-only-key",
    })
    assert openai_config.ai_provider == "openai"
    assert ProviderSelection.from_settings(openai_config).api_key == "openai-only-key"
    assert not openai_config.openrouter_api_key


def test_openrouter_uses_sdk_base_url_and_disables_retries(monkeypatch: pytest.MonkeyPatch,
                                                          tmp_path: Path) -> None:
    from cyberpath_ai.ai import providers
    captured = {}

    def fake_sdk(**kwargs: object) -> object:
        captured.update(kwargs)
        return router_client()

    monkeypatch.setattr(providers, "AsyncOpenAI", fake_sdk)
    selection = ProviderSelection.from_settings(settings(tmp_path / "db.sqlite"))
    ProviderAdapter(selection, 12).client()
    assert captured["base_url"] == OPENROUTER_BASE_URL
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 12
    assert captured["api_key"] == "router-test"


def test_real_sdk_mock_transport_handles_virtual_router_and_typed_metadata() -> None:
    """Regression: cast_to=dict raised ValueError inside openai's parser."""
    async def exercise() -> None:
        seen = []

        async def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.url.path.endswith("/key"):
                return httpx.Response(200, json={"data": {"label": "valid key"}})
            return httpx.Response(200, json={"data": {
                "id": "vendor/model:free",
                "architecture": {"output_modalities": ["text"]},
                "pricing": {"prompt": "0", "completion": "0", "request": "0"},
            }})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
            client = AsyncOpenAI(api_key="fake", base_url=OPENROUTER_BASE_URL,
                                 http_client=http_client, max_retries=0)
            virtual = ProviderAdapter(ProviderSelection("openrouter", "openrouter/free", "fake"),
                                      5, client)
            probe = await virtual.check_model()
            assert probe.available and probe.virtual_router
            assert seen == ["/api/v1/key"]
            fixed = ProviderAdapter(ProviderSelection("openrouter", "vendor/model:free", "fake"),
                                    5, client)
            probe = await fixed.check_model()
            assert probe.available and not probe.compatibility_issue
            assert seen[-1] == "/api/v1/model/vendor/model:free"
    asyncio.run(exercise())


def test_virtual_router_with_real_sdk_transport_reaches_chat_generation(tmp_path: Path) -> None:
    """A valid key must not be rejected by an unrelated model metadata lookup."""
    async def exercise() -> None:
        seen = []

        async def handle(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            if request.url.path.endswith("/key"):
                return httpx.Response(200, json={"data": {"label": "valid key"}})
            if request.url.path.endswith("/chat/completions"):
                assert json.loads(request.content)["model"] == "openrouter/free"
                return httpx.Response(200, json={
                    "id": "chat-test", "object": "chat.completion", "created": 1,
                    "model": "liquid/lfm-2.5-2.6b:free",
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {
                        "role": "assistant", "content": json.dumps(BODY),
                    }}],
                })
            raise AssertionError("Unexpected provider endpoint")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http_client:
            client = AsyncOpenAI(api_key="fake", base_url=OPENROUTER_BASE_URL,
                                 http_client=http_client, max_retries=0)
            result = await AIExplainer(settings(tmp_path / "db.sqlite"), client=client).explain(ARTICLE)
            assert result == BODY
            assert seen == [("GET", "/api/v1/key"),
                            ("POST", "/api/v1/chat/completions")]
    asyncio.run(exercise())


@pytest.mark.parametrize("exception_name,category", [
    ("AuthenticationError", "authentication"),
    ("PermissionDeniedError", "model_access"),
    ("NotFoundError", "model_access"),
    ("BadRequestError", "compatibility"),
    ("APITimeoutError", "timeout"),
    ("RateLimitError", "rate_limit"),
    ("ValueError", "invalid_response"),
])
def test_provider_error_categories_are_distinct(exception_name: str, category: str) -> None:
    error = ValueError() if exception_name == "ValueError" else type(exception_name, (Exception,), {})()
    selection = ProviderSelection("openrouter", "vendor/model:free", "secret")
    failure = classify_provider_error(error, selection, during_check=True)
    assert failure.category == category
    assert "secret" not in failure.message


def test_openrouter_free_generation_and_cache_without_openai_key(tmp_path: Path) -> None:
    async def exercise() -> None:
        fake = router_client(response=chat_response("```json\n" + json.dumps(BODY) + "\n```"))
        explainer = AIExplainer(settings(tmp_path / "db.sqlite"), client=fake)
        assert await explainer.explain(ARTICLE) == BODY
        assert await explainer.explain(ARTICLE) == BODY
        fake.get.assert_awaited_once_with("key", cast_to=dict[str, object])
        fake.chat.completions.create.assert_awaited_once()
        fake.responses.create.assert_not_awaited()
        request = fake.chat.completions.create.await_args.kwargs
        assert request["model"] == "openrouter/free"
        assert request["max_tokens"] == 1400
        assert request["n"] == 1 and request["stream"] is False
        assert "response_format" not in request and "plugins" not in request
        assert "tools" not in request and "models" not in request
        assert "Level: Beginner" in request["messages"][1]["content"]
        assert "untrusted" in request["messages"][0]["content"]
    asyncio.run(exercise())


def test_provider_switch_uses_distinct_cache_entries(tmp_path: Path) -> None:
    async def exercise() -> None:
        path = tmp_path / "shared.sqlite"
        router = router_client()
        await AIExplainer(settings(path), client=router).explain(ARTICLE)
        openai = SimpleNamespace(
            models=SimpleNamespace(retrieve=AsyncMock()),
            responses=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
                status="completed", output_text=json.dumps(BODY)))),
        )
        await AIExplainer(settings(path, provider="openai", router_key="",
                                   openai_key="openai-test"), client=openai).explain(ARTICLE)
        openai.responses.create.assert_awaited_once()
        router.chat.completions.create.assert_awaited_once()
        # A missing selected key must not silently switch to the other provider.
        with pytest.raises(ExplainError, match="OPENROUTER_API_KEY"):
            await AIExplainer(settings(tmp_path / "missing.sqlite", router_key="",
                                       openai_key="openai-test"), client=router).explain(ARTICLE)
    asyncio.run(exercise())


def test_existing_openai_cache_remains_readable_without_api_key(tmp_path: Path) -> None:
    async def exercise() -> None:
        path = tmp_path / "old.sqlite"
        ExplanationStore(path).save(ARTICLE, "beginner", "gpt-6-luna", BODY)
        explainer = AIExplainer(settings(path, provider="openai", router_key="",
                                         openai_key=""))
        assert await explainer.explain(ARTICLE) == BODY
    asyncio.run(exercise())


def test_free_pricing_change_blocks_generation(tmp_path: Path) -> None:
    async def exercise() -> None:
        fake = router_client(price="0.1")
        fake.get.return_value = {"data": {
            "id": "vendor/model:free",
            "architecture": {"output_modalities": ["text"]},
            "pricing": {"prompt": "0.1", "completion": "0", "request": "0"},
        }}
        with pytest.raises(ExplainError, match="Free pricing could not be confirmed"):
            await AIExplainer(settings(tmp_path / "db.sqlite", router_model="vendor/model:free"),
                              client=fake).explain(ARTICLE)
        fake.chat.completions.create.assert_not_awaited()
    asyncio.run(exercise())


def test_openrouter_rate_limit_and_invalid_chat_responses(tmp_path: Path) -> None:
    async def exercise() -> None:
        fake = router_client()
        fake.chat.completions.create.side_effect = type("RateLimitError", (Exception,), {})()
        with pytest.raises(ExplainError, match="free quota"):
            await AIExplainer(settings(tmp_path / "rate.sqlite"), client=fake).explain(ARTICLE)
        fake = router_client(response=chat_response("not json"))
        with pytest.raises(ExplainError, match="unreadable"):
            await AIExplainer(settings(tmp_path / "invalid.sqlite"), client=fake).explain(ARTICLE)
        with pytest.raises(ExplainError, match="incomplete"):
            parse_chat_response(chat_response(json.dumps(BODY), finish="length"))
    asyncio.run(exercise())


def test_model_checker_reports_selected_provider_and_compatibility(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-router-key")
    fake = router_client()

    class FakeSDK:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["base_url"] == OPENROUTER_BASE_URL
            assert kwargs["api_key"] == "secret-router-key"
            self.get = fake.get

        async def __aenter__(self) -> "FakeSDK":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(model_check, "AsyncOpenAI", FakeSDK)
    assert asyncio.run(model_check.check_model()) == 0
    output = capsys.readouterr().out
    assert "Selected provider: openrouter" in output
    assert "Configured model: openrouter/free" in output
    assert "Credentials present: yes" in output
    assert "Model available: virtual router" in output
    assert "Compatibility issue: none" in output
    assert "secret-router-key" not in output
    fake.get.side_effect = type("AuthenticationError", (Exception,), {})()
    assert asyncio.run(model_check.check_model()) == 1
    failed = capsys.readouterr().out
    assert "Failure category: authentication" in failed
    assert "Model available: not confirmed" in failed
    fake.get.side_effect = None
    monkeypatch.setenv("OPENROUTER_MODEL", "secret-router-key")
    assert asyncio.run(model_check.check_model()) == 2
    redacted = capsys.readouterr().out
    assert "<redacted" in redacted
    assert "secret-router-key" not in redacted
