"""Cache policy, recent filtering and graceful fallback."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
import math

from cyberpath_ai.config import Settings
from cyberpath_ai.news.models import NewsItem, deduplicate
from cyberpath_ai.news.sources import NewsFetcher
from cyberpath_ai.news.storage import NewsStore


logger = logging.getLogger("cyberpath_ai.news.service")


@dataclass(frozen=True)
class NewsResult:
    items: tuple[NewsItem, ...]
    failed_sources: tuple[str, ...] = ()
    from_cache: bool = False
    cooldown_seconds: int = 0
    general_error: bool = False

    @property
    def failed(self) -> bool:
        return (bool(self.failed_sources) or self.general_error) and not self.items


class NewsService:
    def __init__(
        self,
        settings: Settings,
        store: NewsStore | None = None,
        fetcher: NewsFetcher | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or NewsStore(settings.news_db_path)
        self.fetcher = fetcher or NewsFetcher(
            settings.news_http_timeout_seconds, settings.news_nvd_max_pages
        )
        self._lock = asyncio.Lock()

    async def get_news(self, *, refresh: bool = False, now: datetime | None = None) -> NewsResult:
        async with self._lock:
            current = (now or datetime.now(UTC)).astimezone(UTC)
            lookback = timedelta(hours=self.settings.news_lookback_hours)
            last_attempt, last_success, previous_failures = self.store.fetch_state()
            cooldown = timedelta(seconds=self.settings.news_refresh_cooldown_seconds)
            ttl = timedelta(minutes=self.settings.news_cache_minutes)
            within_cooldown = last_attempt is not None and current - last_attempt < cooldown
            fresh_cache = (
                last_success is not None
                and current - last_success < ttl
                and not previous_failures
            )
            if within_cooldown or (fresh_cache and not refresh):
                result = NewsResult(
                    tuple(self.store.recent(current, lookback)[:5]),
                    previous_failures,
                    from_cache=True,
                    cooldown_seconds=(
                        max(1, math.ceil((cooldown - (current - last_attempt)).total_seconds()))
                        if refresh and within_cooldown and last_attempt is not None
                        else 0
                    ),
                )
                logger.info(json.dumps({
                    "event": "news_cache_used",
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "reason": "cooldown" if within_cooldown else "ttl",
                    "articles_shown": len(result.items),
                    "failed_sources": list(previous_failures),
                    "cooldown_seconds": result.cooldown_seconds,
                }, sort_keys=True))
                return result

            try:
                fetched, failures = await self.fetcher.fetch_all(current, lookback)
            except (OSError, ValueError, asyncio.TimeoutError) as error:
                logger.warning(json.dumps({
                    "event": "news_fetch_unexpected_failure",
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                    "error_type": type(error).__name__,
                }, sort_keys=True))
                return NewsResult(
                    tuple(self.store.recent(current, lookback)[:5]),
                    from_cache=True,
                    general_error=True,
                )
            recent = deduplicate([item for item in fetched if item.is_recent(current, lookback)])
            self.store.save(recent, current)
            self.store.record_fetch(current, failures)
            return NewsResult(tuple(self.store.recent(current, lookback)[:5]), failures)
