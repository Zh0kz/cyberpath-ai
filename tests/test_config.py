import pytest

from cyberpath_ai.config import Settings


def test_settings_load_allowlist_without_exposing_token() -> None:
    settings = Settings.from_env(
        {"TELEGRAM_BOT_TOKEN": "private-token", "ALLOWED_USER_IDS": "42, 7,42"}
    )

    assert settings.allowed_user_ids == frozenset({7, 42})
    assert "private-token" not in repr(settings)


def test_ai_configuration_keeps_key_private_and_rejects_empty_model() -> None:
    values = {"TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": "42",
              "OPENAI_API_KEY": "private-openai-key", "OPENAI_MODEL": "gpt-6-luna"}
    settings = Settings.from_env(values)
    assert settings.openai_model == "gpt-6-luna"
    assert "private-openai-key" not in repr(settings)
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        Settings.from_env({**values, "AI_PROVIDER": "openai", "OPENAI_MODEL": "  "})


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"TELEGRAM_BOT_TOKEN": "test"},
        {"TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": ""},
        {"TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": "1,abc"},
        {"TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": "0"},
        {"TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": "1,"},
    ],
)
def test_settings_reject_missing_or_invalid_security_configuration(values: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Settings.from_env(values)


def test_settings_reject_lookback_beyond_nvd_date_range() -> None:
    with pytest.raises(ValueError, match="120-day"):
        Settings.from_env({
            "TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": "42",
            "NEWS_LOOKBACK_HOURS": "2881",
        })


def test_daily_defaults_and_timezone_configuration() -> None:
    values = {"TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": "42"}
    defaults = Settings.from_env(values)
    assert (defaults.default_timezone, defaults.daily_digest_hour) == ("Asia/Almaty", 9)
    configured = Settings.from_env({**values, "DEFAULT_TIMEZONE": "Europe/Berlin",
                                    "DAILY_DIGEST_TIME": "18:00"})
    assert (configured.default_timezone, configured.daily_digest_hour) == ("Europe/Berlin", 18)
    for invalid in ("9:00", "24:00", "09:15", "invalid"):
        with pytest.raises(ValueError, match="DAILY_DIGEST_TIME"):
            Settings.from_env({**values, "DAILY_DIGEST_TIME": invalid})
    with pytest.raises(ValueError, match="DEFAULT_TIMEZONE"):
        Settings.from_env({**values, "DEFAULT_TIMEZONE": "No/Such_Zone"})


def test_review_configuration_is_bounded_and_environment_independent() -> None:
    base = {"TELEGRAM_BOT_TOKEN": "test", "ALLOWED_USER_IDS": "42"}
    defaults = Settings.from_env(base)
    assert defaults.review_success_intervals_days == (3, 7, 14, 30)
    assert defaults.review_failure_interval_days == 1
    custom = Settings.from_env({**base, "REVIEW_SUCCESS_INTERVALS_DAYS": "2,5,11",
                                "REVIEW_FAILURE_INTERVAL_DAYS": "2",
                                "REVIEW_SESSION_SIZE": "4"})
    assert (custom.review_success_intervals_days, custom.review_failure_interval_days,
            custom.review_session_size) == ((2, 5, 11), 2, 4)
    for invalid in ("", "0,3", "3,3", "7,3", "one,two"):
        with pytest.raises(ValueError, match="REVIEW_SUCCESS_INTERVALS_DAYS"):
            Settings.from_env({**base, "REVIEW_SUCCESS_INTERVALS_DAYS": invalid})
    with pytest.raises(ValueError, match="REVIEW_SESSION_SIZE"):
        Settings.from_env({**base, "REVIEW_SESSION_SIZE": "11"})
