"""Offline daily-digest persistence, scheduling, and Telegram UI tests."""

import asyncio
from datetime import UTC, date, datetime, timedelta
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.service import NewsResult
from cyberpath_ai.news.storage import NewsStore
from cyberpath_ai.scheduler import (
    DailyScheduler, DigestService, PreferenceStore, format_digest, next_run_utc, parse_hour,
)


NOW = datetime(2026, 10, 21, 4, 0, tzinfo=UTC)


def config(path: Path, users: frozenset[int] = frozenset({42, 43})) -> Settings:
    return Settings("123456:TEST", users, news_db_path=path,
                    ai_provider="openrouter", openrouter_api_key="")


def item(index: int, published: datetime = NOW) -> NewsItem:
    return NewsItem("BleepingComputer", f"Source headline {index} <check>",
                    f"Factual source description {index} & details", published,
                    f"https://www.bleepingcomputer.com/news/security/story-{index}/")


def active_store(path: Path, users: tuple[int, ...] = (42,)) -> PreferenceStore:
    store = PreferenceStore(path)
    for user_id in users:
        store.touch(user_id, user_id + 1000)
        store.set_enabled(user_id, True)
    return store


def test_defaults_opt_in_persistence_and_time_validation(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    store = PreferenceStore(path)
    store.touch(42, 1042)
    pref = store.get(42)
    assert (pref.timezone, pref.hour, pref.article_count, pref.enabled, pref.chat_id) == (
        "Asia/Almaty", 9, 3, False, 1042)
    assert store.active(frozenset({42})) == []
    assert store.set_enabled(42, True)
    store.set_hour(42, 18)
    store.set_article_count(42, 5)
    store.touch(42, 2042)
    restarted = PreferenceStore(path)
    assert (restarted.get(42).hour, restarted.get(42).article_count,
            restarted.get(42).enabled, restarted.get(42).chat_id) == (18, 5, True, 2042)
    assert parse_hour("00") == 0 and parse_hour("23") == 23
    for invalid in ("9", "9:00", "24", "-1", "ab", "\u0660\u0669"):
        assert parse_hour(invalid) is None
    restarted.set_enabled(42, False)
    assert not PreferenceStore(path).get(42).enabled


def test_local_timezone_next_run_and_no_backlog(tmp_path: Path) -> None:
    store = active_store(tmp_path / "db.sqlite")
    pref = store.get(42)
    before = datetime(2026, 10, 21, 3, 59, tzinfo=UTC)
    due = next_run_utc(pref, before)
    assert due.astimezone(ZoneInfo("Asia/Almaty")).strftime("%Y-%m-%d %H:%M") == "2026-10-21 09:00"
    restarted = PreferenceStore(tmp_path / "db.sqlite")
    late = due + timedelta(minutes=1)
    assert next_run_utc(restarted.get(42), late).date() == (due + timedelta(days=1)).date()


def test_schedule_creation_multiple_users_and_allowlist(tmp_path: Path) -> None:
    settings = config(tmp_path / "db.sqlite", frozenset({42, 43}))
    store = active_store(settings.news_db_path, (42, 43, 99))
    news = SimpleNamespace(get_news=AsyncMock(return_value=NewsResult(())))
    digest = DigestService(settings, store, news)
    scheduler = DailyScheduler(settings, store, digest)
    plan = scheduler.planned(datetime(2026, 10, 21, 3, 59, tzinfo=UTC))
    assert [pref.user_id for _, pref in plan] == [42, 43]
    assert plan[0][0] == plan[1][0]
    store.set_hour(43, 10)
    assert scheduler.planned(datetime(2026, 10, 21, 3, 59, tzinfo=UTC))[1][0] > plan[0][0]
    store.set_enabled(42, False)
    assert [pref.user_id for _, pref in scheduler.planned(NOW)] == [43]


def test_claim_is_unique_across_restarts_and_users(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    store = active_store(path, (42, 43))
    first = store.claim(42, date(2026, 10, 21), "scheduled")
    assert first is not None
    store.mark(first, "sending")
    restarted = PreferenceStore(path)
    restarted.reconcile_interrupted()
    assert restarted.deliveries(42)[0]["status"] == "unknown"
    assert restarted.claim(42, date(2026, 10, 21), "scheduled") is None
    assert restarted.claim(43, date(2026, 10, 21), "scheduled") is not None
    assert restarted.claim(42, date(2026, 10, 21), "test") is not None
    assert restarted.claim(42, date(2026, 10, 21), "test") is not None
    assert restarted.claim(42, date(2026, 10, 22), "scheduled") is not None


def test_digest_format_has_source_buttons_and_no_preview(tmp_path: Path) -> None:
    articles = tuple(item(i) for i in range(5))
    text, keyboard = format_digest(articles, date(2026, 10, 21), cached=True, older=False)
    assert "2026-10-21" in text and "сохранённые публикации" in text
    assert "&lt;check&gt;" in text and "&amp; details" in text
    assert len(text) < 4096
    assert [row[0].url for row in keyboard.inline_keyboard] == [a.source_url for a in articles]
    text, keyboard = format_digest((), date(2026, 10, 21), cached=False, older=False)
    assert "нет подходящих новостей с подтверждённым источником" in text and keyboard is None
    long_articles = tuple(NewsItem("<Source>", "<&>" * 1000, "<&>" * 1000,
                                   NOW, f"https://example.com/{index}") for index in range(5))
    long_text, _ = format_digest(long_articles, date(2026, 10, 21),
                                 cached=True, older=True)
    assert len(long_text) < 4096


def test_delivery_missing_news_cache_and_manual_test(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = config(tmp_path / "db.sqlite")
        store = active_store(settings.news_db_path)
        news = SimpleNamespace(get_news=AsyncMock(return_value=NewsResult(())))
        digest = DigestService(settings, store, news)
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
        assert await digest.deliver(bot, 42, scheduled=True, now=NOW) == "sent"
        assert "нет подходящих новостей с подтверждённым источником" in bot.send_message.await_args.args[1]
        assert bot.send_message.await_args.kwargs["link_preview_options"].is_disabled
        assert store.deliveries(42)[0]["status"] == "sent"
        assert await digest.deliver(bot, 42, scheduled=True, now=NOW) == "already_claimed"
        assert bot.send_message.await_count == 1
        news.get_news.return_value = NewsResult((item(1),), from_cache=True)
        assert await digest.deliver(bot, 42, scheduled=False, now=NOW) == "sent"
        assert "сохранённые публикации" in bot.send_message.await_args.args[1]
        assert bot.send_message.await_count == 2
        assert [row["kind"] for row in store.deliveries(42)] == ["scheduled", "test"]
        assert await digest.deliver(bot, 99, scheduled=False, now=NOW) == "not_allowed"
        assert bot.send_message.await_count == 2
    asyncio.run(exercise())


def test_disabled_user_receives_no_scheduled_digest(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = config(tmp_path / "db.sqlite")
        store = PreferenceStore(settings.news_db_path)
        store.touch(42, 1042)
        news = SimpleNamespace(get_news=AsyncMock())
        bot = SimpleNamespace(send_message=AsyncMock())
        assert await DigestService(settings, store, news).deliver(
            bot, 42, scheduled=True, now=NOW) == "disabled"
        bot.send_message.assert_not_awaited()
        news.get_news.assert_not_awaited()
        assert store.deliveries(42) == []
    asyncio.run(exercise())


def test_older_cache_only_on_source_failure(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = config(tmp_path / "db.sqlite")
        store = active_store(settings.news_db_path)
        older = item(1, NOW - timedelta(days=4))
        NewsStore(settings.news_db_path).save([older], NOW)
        news = SimpleNamespace(get_news=AsyncMock(return_value=NewsResult((), ("NVD",))))
        digest = DigestService(settings, store, news)
        bot = SimpleNamespace(send_message=AsyncMock())
        assert await digest.deliver(bot, 42, scheduled=True, now=NOW) == "sent"
        text = bot.send_message.await_args.args[1]
        assert "более старые сохранённые публикации" in text and "Source headline 1" in text
        assert bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0].url == older.source_url
    asyncio.run(exercise())


def test_telegram_error_is_uncertain_and_not_resent(tmp_path: Path, caplog) -> None:
    async def exercise() -> None:
        settings = config(tmp_path / "db.sqlite")
        store = active_store(settings.news_db_path)
        news = SimpleNamespace(get_news=AsyncMock(return_value=NewsResult((item(1),))))
        digest = DigestService(settings, store, news)
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=OSError("private secret")))
        assert await digest.deliver(bot, 42, scheduled=True, now=NOW) == "failed"
        assert store.deliveries(42)[0]["status"] == "unknown"
        assert await DigestService(settings, PreferenceStore(settings.news_db_path), news).deliver(
            bot, 42, scheduled=True, now=NOW) == "already_claimed"
        bot.send_message.assert_awaited_once()
    with caplog.at_level(logging.WARNING, logger="cyberpath_ai.scheduler"):
        asyncio.run(exercise())
    assert "private secret" not in caplog.text


def test_telegram_rate_limit_gets_one_bounded_retry(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = config(tmp_path / "db.sqlite")
        store = active_store(settings.news_db_path)
        news = SimpleNamespace(get_news=AsyncMock(return_value=NewsResult((item(1),))))
        digest = DigestService(settings, store, news)
        rate_limit = TelegramRetryAfter(SendMessage(chat_id=1042, text="test"),
                                        "rate limited", retry_after=0)
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[rate_limit, SimpleNamespace()]))
        assert await digest.deliver(bot, 42, scheduled=True, now=NOW) == "sent"
        assert bot.send_message.await_count == 2
        assert store.deliveries(42)[0]["status"] == "sent"
    asyncio.run(exercise())


def _callback(number: int, user_id: int, data: str) -> Update:
    message = Message(message_id=10, date=NOW, chat=Chat(id=user_id, type="private"),
                      from_user=User(id=100, is_bot=True, first_name="Bot"), text="Settings")
    return Update(update_id=number, callback_query=CallbackQuery(
        id=str(number), from_user=User(id=user_id, is_bot=False, first_name="User"),
        chat_instance="test", data=data, message=message))


def _text(number: int, user_id: int, value: str) -> Update:
    return Update(update_id=number, message=Message(
        message_id=number, date=NOW, chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="User"), text=value))


def test_settings_ui_enable_time_cancel_count_test_and_allowlist(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = config(tmp_path / "db.sqlite", frozenset({42}))
        news = SimpleNamespace(get_news=AsyncMock(return_value=NewsResult((item(1),))))
        dispatcher = create_dispatcher(settings, news_service=news)
        async with Bot(token=settings.telegram_bot_token) as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            updates = [
                _callback(1, 42, "menu:settings"),
                _callback(2, 42, "settings:enable"),
                _callback(3, 42, "settings:count:5"),
                _callback(4, 42, "settings:time"),
                _text(5, 42, "25"),
                _text(6, 42, "/cancel"),
                _callback(7, 42, "settings:time"),
                _text(8, 42, "18"),
                _callback(9, 42, "settings:test"),
                _callback(10, 99, "settings:disable"),
            ]
            for update in updates:
                await dispatcher.feed_update(bot, update)
            pref = PreferenceStore(settings.news_db_path).get(42)
            assert (pref.enabled, pref.hour, pref.article_count, pref.chat_id) == (True, 18, 5, 42)
            assert PreferenceStore(settings.news_db_path).get(99) is None
            assert any(isinstance(call.args[1], EditMessageText) and "Asia/Almaty" in call.args[1].text
                       for call in bot.session.make_request.await_args_list)
            assert any(isinstance(call.args[1], SendMessage) and "Invalid hour" in call.args[1].text
                       for call in bot.session.make_request.await_args_list)
            assert any(isinstance(call.args[1], SendMessage) and "Ежедневные новости" in call.args[1].text
                       for call in bot.session.make_request.await_args_list)
            assert news.get_news.await_count == 1
            await dispatcher.feed_update(bot, _callback(11, 42, "settings:disable"))
            assert not PreferenceStore(settings.news_db_path).get(42).enabled
    asyncio.run(exercise())


def test_scheduler_starts_and_stops_cleanly_without_opt_ins(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = config(tmp_path / "db.sqlite")
        store = PreferenceStore(settings.news_db_path)
        news = SimpleNamespace(get_news=AsyncMock(return_value=NewsResult(())))
        scheduler = DailyScheduler(settings, store, DigestService(settings, store, news))
        bot = SimpleNamespace(send_message=AsyncMock())
        scheduler.start(bot)
        await asyncio.sleep(0)
        await scheduler.stop()
        bot.send_message.assert_not_awaited()
    asyncio.run(exercise())


def test_running_timer_dispatches_due_user_once(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = config(tmp_path / "db.sqlite")
        store = active_store(settings.news_db_path)
        digest = SimpleNamespace(deliver=AsyncMock(return_value="sent"))
        scheduler = DailyScheduler(settings, store, digest)
        due = datetime.now(UTC) + timedelta(milliseconds=80)
        preference = store.get(42)
        calls = 0

        def plan():
            nonlocal calls
            calls += 1
            return [(due, preference)] if calls == 1 else []

        scheduler.planned = plan
        scheduler.start(SimpleNamespace())
        await asyncio.sleep(0.2)
        await scheduler.stop()
        digest.deliver.assert_awaited_once()
        assert digest.deliver.await_args.args[0] is not None
        assert digest.deliver.await_args.args[1] == 42
        assert digest.deliver.await_args.kwargs == {"scheduled": True, "now": due}
    asyncio.run(exercise())
