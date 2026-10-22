"""Keep tests independent of live credentials and external services."""

import ipaddress
import socket

import pytest


APP_ENV_NAMES = (
    "TELEGRAM_BOT_TOKEN", "ALLOWED_USER_IDS", "NEWS_DB_PATH", "NEWS_LOOKBACK_HOURS",
    "NEWS_CACHE_MINUTES", "NEWS_REFRESH_COOLDOWN_SECONDS", "NEWS_HTTP_TIMEOUT_SECONDS",
    "NEWS_NVD_MAX_PAGES", "DEFAULT_TIMEZONE", "DAILY_DIGEST_TIME",
    "MASTERY_THRESHOLD_PERCENT",
    "REVIEW_FAILURE_INTERVAL_DAYS",
    "REVIEW_SUCCESS_INTERVALS_DAYS",
    "REVIEW_SESSION_SIZE",
    "AI_PROVIDER", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENAI_API_KEY",
    "OPENAI_MODEL", "AI_TIMEOUT_SECONDS", "AI_MAX_OUTPUT_TOKENS",
    "MAX_AI_CALLS_PER_DAY", "AI_CONCURRENCY_LIMIT",
)


@pytest.fixture(autouse=True)
def isolate_runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in APP_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast if a test accidentally contacts a real external service."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def is_local(address: object) -> bool:
        if not isinstance(address, tuple) or not address:
            return False
        try:
            return ipaddress.ip_address(address[0]).is_loopback
        except (ValueError, TypeError):
            return False

    def guarded_connect(sock: socket.socket, address: object) -> None:
        if sock.family == getattr(socket, "AF_UNIX", None) or is_local(address):
            return original_connect(sock, address)
        raise AssertionError("Tests may not connect to external services")

    def guarded_connect_ex(sock: socket.socket, address: object) -> int:
        if sock.family == getattr(socket, "AF_UNIX", None) or is_local(address):
            return original_connect_ex(sock, address)
        raise AssertionError("Tests may not connect to external services")

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> list:
        if host is None or host == "localhost" or is_local((host,)):
            return original_getaddrinfo(host, *args, **kwargs)
        raise AssertionError("Tests may not resolve external services")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
