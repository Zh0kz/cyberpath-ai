import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from cyberpath_ai.config import Settings
from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.service import NewsService
from cyberpath_ai.news.storage import NewsStore


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def item(url: str = "https://example.com/story/?utm_source=feed") -> NewsItem:
    return NewsItem("BleepingComputer", "Real source title", "Real description", NOW, url)


def test_store_persists_original_url_and_prevents_duplicate_fetches(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.sqlite3")
    store.save([item()], NOW)
    store.save([item("https://example.com/story/#fragment")], NOW + timedelta(minutes=1))

    assert store.count() == 1
    saved = store.recent(NOW + timedelta(minutes=2), timedelta(hours=72))
    assert saved[0].source_url == "https://example.com/story/#fragment"
    assert saved[0].published_at == NOW


def test_service_cache_refresh_and_failure_fallback(tmp_path: Path) -> None:
    settings = Settings(
        "123456:TEST", frozenset({42}), news_db_path=tmp_path / "news.sqlite3",
        news_cache_minutes=30, news_refresh_cooldown_seconds=60,
    )
    fetcher = AsyncMock()
    fetcher.fetch_all.side_effect = [
        ([item()], ()),
        ([], ("CISA KEV", "NVD", "BleepingComputer")),
    ]
    service = NewsService(settings, fetcher=fetcher)

    first = asyncio.run(service.get_news(now=NOW))
    cooled = asyncio.run(service.get_news(refresh=True, now=NOW + timedelta(seconds=30)))
    cached = asyncio.run(service.get_news(now=NOW + timedelta(minutes=1)))
    failed_refresh = asyncio.run(service.get_news(refresh=True, now=NOW + timedelta(minutes=2)))

    assert len(first.items) == 1
    assert cooled.from_cache
    assert cooled.cooldown_seconds == 30
    assert cached.from_cache
    assert cached.cooldown_seconds == 0
    assert fetcher.fetch_all.await_count == 2
    assert len(failed_refresh.items) == 1
    assert failed_refresh.failed_sources == ("CISA KEV", "NVD", "BleepingComputer")


def test_service_returns_empty_after_successful_empty_fetch(tmp_path: Path) -> None:
    settings = Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "news.sqlite3")
    fetcher = AsyncMock()
    fetcher.fetch_all.return_value = ([], ())

    result = asyncio.run(NewsService(settings, fetcher=fetcher).get_news(now=NOW))

    assert not result.items
    assert not result.failed


def test_unexpected_fetch_error_does_not_claim_specific_source_failed(tmp_path: Path) -> None:
    settings = Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "news.sqlite3")
    store = NewsStore(settings.news_db_path)
    store.save([item()], NOW)
    fetcher = AsyncMock()
    fetcher.fetch_all.side_effect = OSError("internal failure")

    result = asyncio.run(NewsService(settings, store=store, fetcher=fetcher).get_news(now=NOW))

    assert result.general_error
    assert result.failed_sources == ()
    assert len(result.items) == 1
