"""HTTP ingestion from official public sources."""

import asyncio
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import json
import logging
from typing import Any
from xml.etree import ElementTree

import aiohttp

from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.parsing import parse_bleepingcomputer, parse_kev, parse_nvd


KEV_URL = "https://raw.githubusercontent.com/cisagov/kev-data/develop/known_exploited_vulnerabilities.json"
NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
BLEEPINGCOMPUTER_URL = "https://www.bleepingcomputer.com/feed/"
MAX_RESPONSE_BYTES = 24_000_000
logger = logging.getLogger("cyberpath_ai.news.sources")
_http_status: ContextVar[int | None] = ContextVar("news_http_status", default=None)


def _retry_after_seconds(error: aiohttp.ClientResponseError, now: datetime) -> int:
    raw = error.headers.get("Retry-After") if error.headers else None
    if raw:
        try:
            return min(max(int(raw), 1), 3600)
        except ValueError:
            try:
                retry_date = parsedate_to_datetime(raw).astimezone(UTC)
                return min(max(int((retry_date - now).total_seconds()), 1), 3600)
            except (TypeError, ValueError):
                pass
    return 60


def _error_type(error: Exception) -> str:
    if isinstance(error, aiohttp.ClientResponseError):
        return "rate_limited" if error.status == 429 else "http_error"
    if isinstance(error, asyncio.TimeoutError):
        return "timeout"
    if isinstance(error, aiohttp.ClientError):
        return "connection_or_client_error"
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(error, ElementTree.ParseError):
        return "invalid_xml"
    if isinstance(error, ValueError):
        return "invalid_feed"
    return type(error).__name__


def _log_source(
    source: str, outcome: str, http_status: int | None, count: int,
    error_type: str | None = None, retry_after_seconds: int | None = None,
) -> None:
    # Only fixed field names and safe categories: no URLs, response bodies,
    # exception messages, credentials, or Telegram tokens.
    record = {
        "event": "news_source_fetch",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "source": source,
        "outcome": outcome,
        "http_status": http_status,
        "articles_retrieved": count,
        "error_type": error_type,
        "retry_after_seconds": retry_after_seconds,
    }
    logger.log(
        logging.INFO if outcome == "success" else logging.WARNING,
        json.dumps(record, sort_keys=True),
    )


class NewsFetcher:
    def __init__(self, timeout_seconds: int = 12, nvd_max_pages: int = 2) -> None:
        self.timeout_seconds = timeout_seconds
        self.nvd_max_pages = nvd_max_pages
        self._backoff_until: dict[str, datetime] = {}

    async def _get_bytes(
        self, session: aiohttp.ClientSession, url: str, params: dict[str, Any] | None = None
    ) -> bytes:
        async with session.get(url, params=params) as response:
            _http_status.set(getattr(response, "status", None))
            response.raise_for_status()
            body = await response.read()
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("Source response exceeds size limit")
            return body

    async def fetch_kev(self, session: aiohttp.ClientSession) -> list[NewsItem]:
        payload = json.loads(await self._get_bytes(session, KEV_URL))
        return parse_kev(payload)

    async def fetch_nvd(
        self, session: aiohttp.ClientSession, now: datetime, lookback: timedelta
    ) -> list[NewsItem]:
        start = (now - lookback).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000")
        end = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000")
        items: list[NewsItem] = []
        start_index = 0
        page_size = 2000
        for page in range(self.nvd_max_pages):
            if page:
                # NVD asks unauthenticated clients to space requests.
                await asyncio.sleep(6)
            payload = json.loads(
                await self._get_bytes(
                    session,
                    NVD_URL,
                    {
                        "pubStartDate": start,
                        "pubEndDate": end,
                        "resultsPerPage": page_size,
                        "startIndex": start_index,
                    },
                )
            )
            items.extend(parse_nvd(payload))
            returned = len(payload.get("vulnerabilities", []))
            start_index += returned
            if not returned or start_index >= int(payload.get("totalResults", start_index)):
                break
        return items

    async def fetch_bleepingcomputer(self, session: aiohttp.ClientSession) -> list[NewsItem]:
        return parse_bleepingcomputer(await self._get_bytes(session, BLEEPINGCOMPUTER_URL))

    async def fetch_all(
        self, now: datetime, lookback: timedelta
    ) -> tuple[list[NewsItem], tuple[str, ...]]:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        headers = {"User-Agent": "CyberPathAI/0.2.1 (+personal Telegram news reader)"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async def collect(name: str, fetch) -> tuple[list[NewsItem], bool]:
                if now < self._backoff_until.get(name, datetime.min.replace(tzinfo=UTC)):
                    remaining = max(1, int((self._backoff_until[name] - now).total_seconds()))
                    _log_source(name, "skipped", 429, 0, "rate_limit_backoff", remaining)
                    return [], True
                token = _http_status.set(None)
                try:
                    items = await fetch()
                    _log_source(name, "success", _http_status.get(), len(items))
                    return items, False
                except Exception as error:
                    status = (
                        error.status if isinstance(error, aiohttp.ClientResponseError)
                        else _http_status.get()
                    )
                    retry_after = None
                    if isinstance(error, aiohttp.ClientResponseError) and error.status == 429:
                        retry_after = _retry_after_seconds(error, now)
                        self._backoff_until[name] = now + timedelta(seconds=retry_after)
                    _log_source(name, "failure", status, 0, _error_type(error), retry_after)
                    return [], True
                finally:
                    _http_status.reset(token)

            results = await asyncio.gather(
                collect("CISA KEV", lambda: self.fetch_kev(session)),
                collect("NVD", lambda: self.fetch_nvd(session, now, lookback)),
                collect("BleepingComputer", lambda: self.fetch_bleepingcomputer(session)),
            )
        all_items: list[NewsItem] = []
        failed: list[str] = []
        for name, (items, did_fail) in zip(("CISA KEV", "NVD", "BleepingComputer"), results):
            if did_fail:
                failed.append(name)
            else:
                all_items.extend(items)
        return all_items, tuple(failed)
