"""Normalized source records and ranking rules."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
SOURCE_RANK = {"CISA KEV": 3, "BleepingComputer": 2, "NVD": 1}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Source URL must be an absolute HTTP(S) URL")
    hostname = parts.hostname.lower()
    port = parts.port
    if port and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
        hostname = f"{hostname}:{port}"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
        )
    )
    return urlunsplit((parts.scheme.lower(), hostname, path, query, ""))


@dataclass(frozen=True)
class NewsItem:
    source: str
    title: str
    description: str
    published_at: datetime
    source_url: str
    cve_ids: tuple[str, ...] = ()
    date_precision: str = "second"
    severity_rank: int = 0

    def __post_init__(self) -> None:
        if not self.title.strip() or self.published_at.tzinfo is None:
            raise ValueError("News requires a title and timezone-aware date")
        canonical_url(self.source_url)
        if self.date_precision not in {"day", "second"}:
            raise ValueError("Unsupported date precision")
        object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))

    @property
    def canonical_url(self) -> str:
        return canonical_url(self.source_url)

    @property
    def identity_key(self) -> str:
        # A CISA addition and an NVD publication are different events. RSS
        # articles stay separate even when they mention the same CVE.
        if self.source in {"CISA KEV", "NVD"} and self.cve_ids:
            return f"{self.source}:{self.cve_ids[0]}"
        return f"url:{self.canonical_url}"

    def is_recent(self, now: datetime, lookback: timedelta) -> bool:
        now = now.astimezone(UTC)
        cutoff = now - lookback
        if self.date_precision == "day":
            # The source supplies only a calendar date. Include it while that
            # day could overlap the rolling lookback; do not claim an hour.
            return self.published_at < now and self.published_at + timedelta(days=1) > cutoff
        return cutoff <= self.published_at <= now


def sort_news(items: list[NewsItem]) -> list[NewsItem]:
    return sorted(
        items,
        key=lambda item: (
            item.published_at.date(),
            SOURCE_RANK.get(item.source, 0) + item.severity_rank,
            item.published_at,
            item.identity_key,
        ),
        reverse=True,
    )


def deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    unique: dict[str, NewsItem] = {}
    for item in items:
        previous = unique.get(item.identity_key)
        if previous is None or item.published_at > previous.published_at:
            unique[item.identity_key] = item
    return sort_news(list(unique.values()))
