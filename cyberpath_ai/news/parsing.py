"""Parse only fields present in the three source feeds."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import re
from typing import Any
from xml.etree import ElementTree

from cyberpath_ai.news.models import NewsItem


CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)
CISA_CATALOG_URL = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return " ".join(unescape(" ".join(parser.parts)).split())


def cve_ids(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.upper() for match in CVE_RE.findall(text)))


def parse_timestamp(value: str, *, assume_utc: bool = False) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if not assume_utc:
            raise ValueError("Timestamp has no timezone")
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_kev(payload: dict[str, Any]) -> list[NewsItem]:
    items: list[NewsItem] = []
    for entry in payload.get("vulnerabilities", []):
        try:
            cve = str(entry["cveID"]).upper()
            if not CVE_RE.fullmatch(cve):
                continue
            date = datetime.strptime(entry["dateAdded"], "%Y-%m-%d").replace(tzinfo=UTC)
            items.append(
                NewsItem(
                    source="CISA KEV",
                    title=plain_text(entry["vulnerabilityName"]),
                    description=plain_text(entry.get("shortDescription", "")),
                    published_at=date,
                    source_url=CISA_CATALOG_URL,
                    cve_ids=(cve,),
                    date_precision="day",
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return items


def parse_nvd(payload: dict[str, Any]) -> list[NewsItem]:
    items: list[NewsItem] = []
    for wrapper in payload.get("vulnerabilities", []):
        try:
            cve_data = wrapper["cve"]
            cve = str(cve_data["id"]).upper()
            if not CVE_RE.fullmatch(cve) or cve_data.get("vulnStatus") == "Rejected":
                continue
            descriptions = cve_data.get("descriptions", [])
            description = next(
                (entry["value"] for entry in descriptions if entry.get("lang") == "en"),
                "",
            )
            metrics = cve_data.get("metrics", {})
            severities = [
                str(metric.get("cvssData", {}).get("baseSeverity", "")).upper()
                for group in metrics.values()
                if isinstance(group, list)
                for metric in group
                if isinstance(metric, dict)
            ]
            severity_rank = max(
                ({"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}.get(level, 0) for level in severities),
                default=0,
            )
            items.append(
                NewsItem(
                    source="NVD",
                    title=cve,
                    description=plain_text(description),
                    # NVD's API publishes UTC timestamps without a suffix.
                    published_at=parse_timestamp(cve_data["published"], assume_utc=True),
                    source_url=f"https://nvd.nist.gov/vuln/detail/{cve}",
                    cve_ids=(cve,),
                    severity_rank=severity_rank,
                )
            )
        except (KeyError, TypeError, ValueError, StopIteration):
            continue
    return items


def parse_bleepingcomputer(xml_bytes: bytes) -> list[NewsItem]:
    root = ElementTree.fromstring(xml_bytes)
    items: list[NewsItem] = []
    for entry in root.findall("./channel/item"):
        try:
            title = plain_text(entry.findtext("title") or "")
            description = plain_text(entry.findtext("description") or "")
            link = (entry.findtext("link") or "").strip()
            pub_date = parsedate_to_datetime(entry.findtext("pubDate") or "")
            if pub_date.tzinfo is None:
                continue
            items.append(
                NewsItem(
                    source="BleepingComputer",
                    title=title,
                    description=description,
                    published_at=pub_date,
                    source_url=link,
                    cve_ids=cve_ids(f"{title} {description}"),
                )
            )
        except (TypeError, ValueError):
            continue
    return items
