from datetime import UTC, datetime, timedelta

from cyberpath_ai.news.models import NewsItem, canonical_url, deduplicate, sort_news
from cyberpath_ai.news.parsing import parse_bleepingcomputer, parse_kev, parse_nvd


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def test_parse_kev_uses_date_added_and_official_catalog_link() -> None:
    items = parse_kev(
        {
            "vulnerabilities": [
                {
                    "cveID": "CVE-2026-12345",
                    "dateAdded": "2026-10-20",
                    "vulnerabilityName": "Example Product <b>Issue</b>",
                    "shortDescription": "An observed issue.",
                },
                {"cveID": "invalid", "dateAdded": "2026-10-20"},
            ]
        }
    )

    assert len(items) == 1
    assert items[0].published_at == datetime(2026, 10, 20, tzinfo=UTC)
    assert items[0].date_precision == "day"
    assert items[0].source_url.startswith("https://www.cisa.gov/")
    assert items[0].title == "Example Product Issue"


def test_parse_nvd_utc_description_and_severity() -> None:
    items = parse_nvd(
        {
            "vulnerabilities": [
                {
                    "cve": {
                        "id": "CVE-2026-12345",
                        "published": "2026-10-21T09:30:00.000",
                        "descriptions": [
                            {"lang": "es", "value": "Spanish"},
                            {"lang": "en", "value": "English description."},
                        ],
                        "metrics": {"cvssMetricV31": [{"cvssData": {"baseSeverity": "HIGH"}}]},
                    }
                },
                {"cve": {"id": "CVE-2026-67890", "vulnStatus": "Rejected"}},
            ]
        }
    )

    assert len(items) == 1
    assert items[0].published_at == datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
    assert items[0].description == "English description."
    assert items[0].severity_rank == 2
    assert items[0].source_url.endswith("/CVE-2026-12345")


def test_parse_rss_preserves_original_url_and_converts_timezone() -> None:
    xml = b"""<rss><channel><item>
      <title>Patch for CVE-2026-12345</title>
      <description><![CDATA[<p>Vendor released a fix.</p>]]></description>
      <link>https://www.bleepingcomputer.com/news/security/example/?utm_source=rss</link>
      <pubDate>Wed, 21 Oct 2026 13:00:00 +0200</pubDate>
    </item><item><title>Missing date</title><link>https://example.org/x</link></item>
    </channel></rss>"""

    items = parse_bleepingcomputer(xml)

    assert len(items) == 1
    assert items[0].published_at == datetime(2026, 10, 21, 11, tzinfo=UTC)
    assert items[0].description == "Vendor released a fix."
    assert items[0].source_url.endswith("?utm_source=rss")
    assert items[0].canonical_url.endswith("/example")
    assert items[0].cve_ids == ("CVE-2026-12345",)


def test_recent_filter_respects_exact_time_and_date_only_overlap() -> None:
    exact = NewsItem("NVD", "CVE", "", NOW - timedelta(hours=72), "https://nvd.nist.gov/x")
    too_old = NewsItem("NVD", "Old", "", NOW - timedelta(hours=73), "https://nvd.nist.gov/y")
    day_only = NewsItem(
        "CISA KEV", "Added", "", datetime(2026, 10, 18, tzinfo=UTC),
        "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
        ("CVE-2026-11111",), "day",
    )

    assert exact.is_recent(NOW, timedelta(hours=72))
    assert not too_old.is_recent(NOW, timedelta(hours=72))
    assert day_only.is_recent(NOW, timedelta(hours=72))


def test_dedup_by_canonical_url_without_merging_distinct_cve_articles() -> None:
    first = NewsItem(
        "BleepingComputer", "First", "", NOW,
        "https://www.bleepingcomputer.com/news/security/story/?utm_source=rss",
        ("CVE-2026-12345",),
    )
    duplicate = NewsItem(
        "BleepingComputer", "Duplicate", "", NOW,
        "https://www.bleepingcomputer.com/news/security/story/#section",
        ("CVE-2026-12345",),
    )
    different_article = NewsItem(
        "BleepingComputer", "Another story", "", NOW,
        "https://www.bleepingcomputer.com/news/security/other/",
        ("CVE-2026-12345",),
    )
    nvd = NewsItem("NVD", "CVE-2026-12345", "", NOW, "https://nvd.nist.gov/vuln/detail/CVE-2026-12345", ("CVE-2026-12345",))

    unique = deduplicate([first, duplicate, different_article, nvd])

    assert len(unique) == 3
    assert canonical_url(first.source_url) == canonical_url(duplicate.source_url)


def test_sort_prioritizes_recent_day_then_source_relevance() -> None:
    recent_nvd = NewsItem("NVD", "Recent CVE", "", NOW, "https://nvd.nist.gov/recent")
    same_day_kev = NewsItem(
        "CISA KEV", "Exploited", "", datetime(2026, 10, 21, tzinfo=UTC),
        "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
        ("CVE-2026-11111",), "day",
    )
    older = NewsItem("NVD", "Older", "", NOW - timedelta(days=1), "https://nvd.nist.gov/older")

    assert sort_news([older, recent_nvd, same_day_kev]) == [same_day_kev, recent_nvd, older]
