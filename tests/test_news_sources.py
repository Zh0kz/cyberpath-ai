import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging

import aiohttp

from cyberpath_ai.news.sources import (
    BLEEPINGCOMPUTER_URL, KEV_URL, NVD_URL, NewsFetcher,
)


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


class FakeResponse:
    def __init__(self, body: bytes | Exception) -> None:
        self.body = body
        self.status = (
            body.status if isinstance(body, aiohttp.ClientResponseError)
            else None if isinstance(body, Exception) else 200
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def raise_for_status(self) -> None:
        if isinstance(self.body, Exception):
            raise self.body

    async def read(self) -> bytes:
        assert isinstance(self.body, bytes)
        return self.body


class FakeSession:
    def __init__(self, responses: dict[str, bytes | Exception], **_) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def get(self, url: str, params: dict | None = None) -> FakeResponse:
        self.requests.append((url, params))
        return FakeResponse(self.responses[url])


def test_fetcher_reads_three_mocked_sources_and_nvd_date_window(monkeypatch, caplog) -> None:
    responses = {
        KEV_URL: json.dumps({"vulnerabilities": [{
            "cveID": "CVE-2026-12345", "dateAdded": "2026-10-20",
            "vulnerabilityName": "Example flaw", "shortDescription": "Observed exploitation.",
        }]}).encode(),
        NVD_URL: json.dumps({"totalResults": 1, "vulnerabilities": [{"cve": {
            "id": "CVE-2026-54321", "published": "2026-10-21T10:00:00.000",
            "descriptions": [{"lang": "en", "value": "NVD description."}],
        }}]}).encode(),
        BLEEPINGCOMPUTER_URL: b"""<rss><channel><item><title>News headline</title>
            <description>News description.</description>
            <link>https://www.bleepingcomputer.com/news/security/story/</link>
            <pubDate>Wed, 21 Oct 2026 11:00:00 GMT</pubDate>
            </item></channel></rss>""",
    }
    session = FakeSession(responses)
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **_: session)

    with caplog.at_level(logging.INFO, logger="cyberpath_ai.news.sources"):
        items, failed = asyncio.run(NewsFetcher(nvd_max_pages=1).fetch_all(NOW, timedelta(hours=72)))

    assert not failed
    assert {item.source for item in items} == {"CISA KEV", "NVD", "BleepingComputer"}
    nvd_params = next(params for url, params in session.requests if url == NVD_URL)
    assert nvd_params["pubStartDate"] == "2026-10-18T12:00:00.000"
    assert nvd_params["pubEndDate"] == "2026-10-21T12:00:00.000"
    logs = [json.loads(record.message) for record in caplog.records
            if record.message.startswith("{")]
    assert all(record["outcome"] == "success" and record["http_status"] == 200
               for record in logs)
    assert all(record["articles_retrieved"] == 1 for record in logs)


def test_fetcher_survives_rate_limit_and_missing_feed(monkeypatch) -> None:
    responses = {
        KEV_URL: b'{"vulnerabilities": []}',
        NVD_URL: aiohttp.ClientError("rate limited"),
        BLEEPINGCOMPUTER_URL: asyncio.TimeoutError(),
    }
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **_: FakeSession(responses))

    items, failed = asyncio.run(NewsFetcher(nvd_max_pages=1).fetch_all(NOW, timedelta(hours=72)))

    assert items == []
    assert failed == ("NVD", "BleepingComputer")


def test_structured_diagnostics_record_http_429_and_respect_retry_after(
    monkeypatch, caplog
) -> None:
    rate_limit = aiohttp.ClientResponseError(
        request_info=None, history=(), status=429,
        headers={"Retry-After": "120"},
    )
    session = FakeSession({
        KEV_URL: b'{"vulnerabilities": []}',
        NVD_URL: rate_limit,
        BLEEPINGCOMPUTER_URL: b"<rss><channel/></rss>",
    })
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **_: session)
    fetcher = NewsFetcher(nvd_max_pages=1)

    with caplog.at_level(logging.INFO, logger="cyberpath_ai.news.sources"):
        _, first_failures = asyncio.run(fetcher.fetch_all(NOW, timedelta(hours=72)))
        _, second_failures = asyncio.run(
            fetcher.fetch_all(NOW + timedelta(seconds=30), timedelta(hours=72))
        )

    assert first_failures == second_failures == ("NVD",)
    assert len([url for url, _ in session.requests if url == NVD_URL]) == 1
    records = [json.loads(record.message) for record in caplog.records
               if record.message.startswith("{")]
    assert {record["source"] for record in records} == {
        "CISA KEV", "NVD", "BleepingComputer"
    }
    first_nvd = next(record for record in records
                     if record["source"] == "NVD" and record["outcome"] == "failure")
    assert first_nvd["http_status"] == 429
    assert first_nvd["articles_retrieved"] == 0
    assert first_nvd["error_type"] == "rate_limited"
    assert first_nvd["retry_after_seconds"] == 120
    assert any(record["source"] == "NVD" and record["outcome"] == "skipped"
               for record in records)
    assert all("token" not in record.message.lower() for record in caplog.records)
