"""Environment-only configuration. No secrets are logged or embedded."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    news_db_path: Path = Path("data/cyberpath.sqlite3")
    news_lookback_hours: int = 72
    news_cache_minutes: int = 30
    news_refresh_cooldown_seconds: int = 60
    news_http_timeout_seconds: int = 12
    news_nvd_max_pages: int = 2
    default_timezone: str = "Asia/Almaty"
    daily_digest_hour: int = 9
    mastery_threshold_percent: int = 80
    review_failure_interval_days: int = 1
    review_success_intervals_days: tuple[int, ...] = (3, 7, 14, 30)
    review_session_size: int = 5
    # Direct construction keeps Stage 2.2 callers compatible; environment config
    # defaults to the free OpenRouter route below.
    ai_provider: str = "openai"
    openrouter_api_key: str = field(default="", repr=False)
    openrouter_model: str = "openrouter/free"
    openai_api_key: str = field(default="", repr=False)
    openai_model: str = "gpt-6-luna"
    ai_timeout_seconds: int = 30
    ai_max_output_tokens: int = 1400
    max_ai_calls_per_day: int = 10
    ai_concurrency_limit: int = 2

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if environ is None else environ
        token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN must be set")

        raw_ids = values.get("ALLOWED_USER_IDS", "").strip()
        if not raw_ids:
            raise ValueError("ALLOWED_USER_IDS must contain at least one Telegram user ID")

        try:
            ids = frozenset(int(part.strip()) for part in raw_ids.split(","))
        except ValueError as exc:
            raise ValueError("ALLOWED_USER_IDS must be comma-separated integers") from exc
        if not ids or any(user_id <= 0 for user_id in ids):
            raise ValueError("ALLOWED_USER_IDS must contain positive Telegram user IDs")

        def positive_int(name: str, default: int) -> int:
            try:
                value = int(values.get(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} must be a positive integer") from exc
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
            return value

        db_path = values.get("NEWS_DB_PATH", "data/cyberpath.sqlite3").strip()
        if not db_path:
            raise ValueError("NEWS_DB_PATH must not be empty")
        provider = values.get("AI_PROVIDER", "openrouter").strip().lower()
        if provider not in {"openrouter", "openai"}:
            raise ValueError("AI_PROVIDER must be openrouter or openai")
        router_model = values.get("OPENROUTER_MODEL", "openrouter/free").strip()
        if provider == "openrouter" and not router_model:
            raise ValueError("OPENROUTER_MODEL must not be empty")
        ai_model = values.get("OPENAI_MODEL", "gpt-6-luna").strip()
        if provider == "openai" and not ai_model:
            raise ValueError("OPENAI_MODEL must not be empty")
        router_model = router_model or "openrouter/free"
        ai_model = ai_model or "gpt-6-luna"

        lookback_hours = positive_int("NEWS_LOOKBACK_HOURS", 72)
        if lookback_hours > 120 * 24:
            raise ValueError("NEWS_LOOKBACK_HOURS cannot exceed NVD's 120-day date range")
        timezone = values.get("DEFAULT_TIMEZONE", "Asia/Almaty").strip()
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("DEFAULT_TIMEZONE must be a valid IANA timezone") from exc
        digest_time = values.get("DAILY_DIGEST_TIME", "09:00").strip()
        if (len(digest_time) != 5 or digest_time[2:] != ":00"
                or not digest_time[:2].isdigit() or not 0 <= int(digest_time[:2]) <= 23):
            raise ValueError("DAILY_DIGEST_TIME must be HH:00 (00:00 to 23:00)")
        mastery_threshold = positive_int("MASTERY_THRESHOLD_PERCENT", 80)
        if mastery_threshold > 100:
            raise ValueError("MASTERY_THRESHOLD_PERCENT must be between 1 and 100")
        raw_intervals = values.get("REVIEW_SUCCESS_INTERVALS_DAYS", "3,7,14,30")
        try:
            success_intervals = tuple(int(part.strip()) for part in raw_intervals.split(","))
        except ValueError as exc:
            raise ValueError("REVIEW_SUCCESS_INTERVALS_DAYS must be positive comma-separated days") from exc
        if (not success_intervals or any(day < 1 for day in success_intervals)
                or tuple(sorted(set(success_intervals))) != success_intervals):
            raise ValueError("REVIEW_SUCCESS_INTERVALS_DAYS must increase strictly")
        review_size = positive_int("REVIEW_SESSION_SIZE", 5)
        if review_size > 10:
            raise ValueError("REVIEW_SESSION_SIZE must be between 1 and 10")

        return cls(
            telegram_bot_token=token,
            allowed_user_ids=ids,
            news_db_path=Path(db_path),
            news_lookback_hours=lookback_hours,
            news_cache_minutes=positive_int("NEWS_CACHE_MINUTES", 30),
            news_refresh_cooldown_seconds=positive_int("NEWS_REFRESH_COOLDOWN_SECONDS", 60),
            news_http_timeout_seconds=positive_int("NEWS_HTTP_TIMEOUT_SECONDS", 12),
            news_nvd_max_pages=positive_int("NEWS_NVD_MAX_PAGES", 2),
            default_timezone=timezone,
            daily_digest_hour=int(digest_time[:2]),
            mastery_threshold_percent=mastery_threshold,
            review_failure_interval_days=positive_int("REVIEW_FAILURE_INTERVAL_DAYS", 1),
            review_success_intervals_days=success_intervals,
            review_session_size=review_size,
            ai_provider=provider,
            openrouter_api_key=values.get("OPENROUTER_API_KEY", "").strip(),
            openrouter_model=router_model,
            openai_api_key=values.get("OPENAI_API_KEY", "").strip(),
            openai_model=ai_model,
            ai_timeout_seconds=positive_int("AI_TIMEOUT_SECONDS", 30),
            ai_max_output_tokens=positive_int("AI_MAX_OUTPUT_TOKENS", 1400),
            max_ai_calls_per_day=positive_int("MAX_AI_CALLS_PER_DAY", 10),
            ai_concurrency_limit=positive_int("AI_CONCURRENCY_LIMIT", 2),
        )
