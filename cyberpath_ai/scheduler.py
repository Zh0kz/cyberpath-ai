"""Opt-in local-time news digests with SQLite delivery claims."""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from html import escape
import json
import logging
from pathlib import Path
import sqlite3
from collections.abc import Iterator
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from cyberpath_ai.config import Settings
from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.service import NewsResult, NewsService
from cyberpath_ai.news.storage import NewsStore
from cyberpath_ai.handlers.navigation import home_button


logger = logging.getLogger("cyberpath_ai.scheduler")
PREVIEW_OFF = LinkPreviewOptions(is_disabled=True)


@dataclass(frozen=True)
class DailyPreference:
    user_id: int
    chat_id: int | None
    timezone: str
    hour: int
    article_count: int
    enabled: bool


def parse_hour(value: str) -> int | None:
    """Accept exactly two ASCII digits in the requested HH format."""
    return int(value) if len(value) == 2 and value.isascii() and value.isdigit() and 0 <= int(value) <= 23 else None


def next_run_utc(preference: DailyPreference, now: datetime) -> datetime:
    local = now.astimezone(ZoneInfo(preference.timezone))
    candidate = datetime.combine(local.date(), time(preference.hour), local.tzinfo)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1),
                                     time(preference.hour), local.tzinfo)
    return candidate.astimezone(UTC)


class PreferenceStore:
    def __init__(self, path: Path, default_timezone: str = "Asia/Almaty",
                 default_hour: int = 9) -> None:
        self.path = path
        self.default_timezone = default_timezone
        self.default_hour = default_hour
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS daily_preferences (
                user_id INTEGER PRIMARY KEY, chat_id INTEGER,
                timezone TEXT NOT NULL, hour INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23),
                article_count INTEGER NOT NULL CHECK(article_count IN (3, 5)),
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1))
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS daily_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                local_date TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('scheduled', 'test')),
                status TEXT NOT NULL, created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL, error_type TEXT
            )""")
            connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS daily_scheduled_once
                ON daily_deliveries(user_id, local_date) WHERE kind = 'scheduled'""")

    def touch(self, user_id: int, chat_id: int) -> None:
        with self._connect() as connection:
            connection.execute("""INSERT INTO daily_preferences
                (user_id, chat_id, timezone, hour, article_count, enabled)
                VALUES (?, ?, ?, ?, 3, 0)
                ON CONFLICT(user_id) DO UPDATE SET chat_id = excluded.chat_id""",
                (user_id, chat_id, self.default_timezone, self.default_hour))

    def get(self, user_id: int) -> DailyPreference | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM daily_preferences WHERE user_id = ?",
                                     (user_id,)).fetchone()
        return self._preference(row) if row else None

    @staticmethod
    def _preference(row: sqlite3.Row) -> DailyPreference:
        return DailyPreference(row["user_id"], row["chat_id"], row["timezone"],
                               row["hour"], row["article_count"], bool(row["enabled"]))

    def active(self, allowed_user_ids: frozenset[int]) -> list[DailyPreference]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM daily_preferences WHERE enabled = 1 AND chat_id IS NOT NULL").fetchall()
        return [self._preference(row) for row in rows if row["user_id"] in allowed_user_ids]

    def set_enabled(self, user_id: int, enabled: bool) -> bool:
        with self._connect() as connection:
            changed = connection.execute("""UPDATE daily_preferences SET enabled = ?
                WHERE user_id = ? AND chat_id IS NOT NULL""",
                (int(enabled), user_id)).rowcount
        return bool(changed)

    def set_hour(self, user_id: int, hour: int) -> None:
        if not 0 <= hour <= 23:
            raise ValueError("Invalid delivery hour")
        with self._connect() as connection:
            connection.execute("UPDATE daily_preferences SET hour = ? WHERE user_id = ?",
                               (hour, user_id))

    def set_article_count(self, user_id: int, count: int) -> None:
        if count not in {3, 5}:
            raise ValueError("Daily article count must be 3 or 5")
        with self._connect() as connection:
            connection.execute("UPDATE daily_preferences SET article_count = ? WHERE user_id = ?",
                               (count, user_id))

    def claim(self, user_id: int, local_date: date, kind: str) -> int | None:
        if kind not in {"scheduled", "test"}:
            raise ValueError("Unknown delivery kind")
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute("""INSERT OR IGNORE INTO daily_deliveries
                (user_id, local_date, kind, status, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, 'reserved', ?, ?)""",
                (user_id, local_date.isoformat(), kind, now, now))
            return cursor.lastrowid if cursor.rowcount else None

    def mark(self, delivery_id: int, status: str, error_type: str | None = None) -> None:
        if status not in {"sending", "sent", "failed", "unknown", "cancelled"}:
            raise ValueError("Invalid delivery status")
        with self._connect() as connection:
            connection.execute("""UPDATE daily_deliveries
                SET status = ?, updated_at_utc = ?, error_type = ? WHERE id = ?""",
                (status, datetime.now(UTC).isoformat(), error_type, delivery_id))

    def deliveries(self, user_id: int) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM daily_deliveries WHERE user_id = ? ORDER BY id",
                                      (user_id,)).fetchall()

    def reconcile_interrupted(self) -> None:
        """Keep in-flight sends claimed after a restart; their outcome is uncertain."""
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("""UPDATE daily_deliveries SET status = 'cancelled',
                updated_at_utc = ? WHERE status = 'reserved'""", (now,))
            connection.execute("""UPDATE daily_deliveries SET status = 'unknown',
                updated_at_utc = ? WHERE status = 'sending'""", (now,))


def _short(value: str, limit: int) -> str:
    output = []
    length = 0
    for character in value:
        encoded = escape(character)
        if length + len(encoded) > limit:
            return "".join(output).rstrip() + "…"
        output.append(encoded)
        length += len(encoded)
    return "".join(output).rstrip()


def format_digest(items: tuple[NewsItem, ...], local_date: date, *, cached: bool,
                  older: bool, include_mission: bool = False) -> tuple[str, InlineKeyboardMarkup | None]:
    heading = f"<b>📰 Ежедневные новости · {local_date.isoformat()}</b>"
    if not items:
        keyboard = (InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎯 Миссия дня", callback_data="menu:mission")],
            [home_button()]])
            if include_mission else None)
        return heading + "\n\nСегодня нет подходящих новостей с подтверждённым источником.", keyboard
    notes = []
    if older:
        notes.append("Источники недоступны; показаны более старые сохранённые публикации.")
    elif cached:
        notes.append("Показаны сохранённые публикации с подтверждёнными источниками.")
    if len(items) < 3:
        notes.append(f"Доступно подходящих публикаций: {len(items)}.")
    parts = [heading, *notes]
    buttons = []
    for index, item in enumerate(items[:5], 1):
        published = item.published_at.strftime("%Y-%m-%d" if item.date_precision == "day"
                                             else "%Y-%m-%d %H:%M UTC")
        parts.append(f"<b>{index}. {_short(item.title, 150)}</b>\n"
                     f"{_short(item.source, 60)} · {published}\n"
                     f"{_short(item.description or 'No source description available.', 300)}\n"
                     f"Оригинал: кнопка {index} ниже")
        buttons.append([InlineKeyboardButton(text=f"{index} · Открыть источник",
                                             url=item.source_url)])
    if include_mission:
        buttons.append([InlineKeyboardButton(text="🎯 Миссия дня", callback_data="menu:mission")])
        buttons.append([home_button()])
    return "\n\n".join(parts), InlineKeyboardMarkup(inline_keyboard=buttons)


class DigestService:
    def __init__(self, settings: Settings, preferences: PreferenceStore,
                 news_service: NewsService, news_store: NewsStore | None = None) -> None:
        self.settings = settings
        self.preferences = preferences
        self.news_service = news_service
        self.news_store = news_store or NewsStore(settings.news_db_path)

    async def _content(self, preference: DailyPreference, now: datetime,
                       local_date: date) -> tuple[str, InlineKeyboardMarkup | None]:
        try:
            result = await self.news_service.get_news(now=now)
        except Exception as error:
            logger.warning(json.dumps({"event": "digest_news_failure",
                                       "error_type": type(error).__name__}))
            result = NewsResult((), general_error=True)
        items = result.items[:preference.article_count]
        older = False
        if not items and (result.failed_sources or result.general_error):
            # A bounded, clearly labeled fallback; no invented or undated news.
            items = tuple(self.news_store.recent(now, timedelta(days=7))[:preference.article_count])
            older = any(not item.is_recent(now, timedelta(hours=self.settings.news_lookback_hours))
                        for item in items)
        return format_digest(items, local_date, cached=result.from_cache or bool(result.failed_sources),
                             older=older, include_mission=True)

    async def deliver(self, bot: Bot, user_id: int, *, scheduled: bool,
                      now: datetime | None = None) -> str:
        if user_id not in self.settings.allowed_user_ids:
            return "not_allowed"
        preference = self.preferences.get(user_id)
        if preference is None or preference.chat_id is None:
            return "no_private_chat"
        if scheduled and not preference.enabled:
            return "disabled"
        current = (now or datetime.now(UTC)).astimezone(UTC)
        local_date = current.astimezone(ZoneInfo(preference.timezone)).date()
        delivery_id = self.preferences.claim(user_id, local_date,
                                             "scheduled" if scheduled else "test")
        if delivery_id is None:
            return "already_claimed"
        try:
            text, keyboard = await self._content(preference, current, local_date)
            if scheduled and not (self.preferences.get(user_id) or preference).enabled:
                self.preferences.mark(delivery_id, "cancelled")
                return "disabled"
            self.preferences.mark(delivery_id, "sending")
            for attempt in range(2):
                try:
                    await bot.send_message(preference.chat_id, text, parse_mode="HTML",
                                           reply_markup=keyboard, link_preview_options=PREVIEW_OFF)
                    self.preferences.mark(delivery_id, "sent")
                    logger.info(json.dumps({"event": "digest_delivery",
                                            "kind": "scheduled" if scheduled else "test",
                                            "status": "sent", "local_date": local_date.isoformat()}))
                    return "sent"
                except TelegramRetryAfter as error:
                    if attempt == 0 and error.retry_after <= 60:
                        await asyncio.sleep(max(0, error.retry_after))
                        continue
                    self.preferences.mark(delivery_id, "failed", "rate_limited")
                    logger.warning(json.dumps({"event": "digest_delivery",
                                               "status": "failed", "error_type": "rate_limited"}))
                    return "failed"
                except Exception as error:
                    # Telegram may have accepted an uncertain network request.
                    # Keep its unique claim to avoid an automatic duplicate.
                    self.preferences.mark(delivery_id, "unknown", type(error).__name__)
                    logger.warning(json.dumps({"event": "digest_delivery",
                                               "status": "unknown", "error_type": type(error).__name__}))
                    return "failed"
        except Exception as error:
            self.preferences.mark(delivery_id, "failed", type(error).__name__)
            logger.warning(json.dumps({"event": "digest_preparation_failure",
                                       "error_type": type(error).__name__}))
            return "failed"
        return "failed"


class DailyScheduler:
    """One in-process async timer; reconstructs only future runs after restart."""

    def __init__(self, settings: Settings, preferences: PreferenceStore,
                 digest: DigestService) -> None:
        self.settings = settings
        self.preferences = preferences
        self.digest = digest
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def planned(self, now: datetime | None = None) -> list[tuple[datetime, DailyPreference]]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        return sorted(((next_run_utc(preference, current), preference)
                       for preference in self.preferences.active(self.settings.allowed_user_ids)),
                      key=lambda entry: (entry[0], entry[1].user_id))

    def wake(self) -> None:
        self._wake.set()

    def start(self, bot: Bot) -> None:
        if self._task is None:
            self.preferences.reconcile_interrupted()
            self._task = asyncio.create_task(self._run(bot), name="cyberpath-daily-news")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self, bot: Bot) -> None:
        while True:
            self._wake.clear()
            try:
                plan = self.planned()
            except Exception as error:
                logger.warning(json.dumps({"event": "digest_schedule_failure",
                                           "error_type": type(error).__name__}))
                await asyncio.sleep(60)
                continue
            if not plan:
                await self._wake.wait()
                continue
            due = plan[0][0]
            delay = max(0.0, (due - datetime.now(UTC)).total_seconds())
            try:
                # Recheck wall time periodically in case the system clock moves.
                await asyncio.wait_for(self._wake.wait(), timeout=min(delay, 60.0))
                continue
            except asyncio.TimeoutError:
                pass
            for scheduled_at, preference in plan:
                if scheduled_at > datetime.now(UTC):
                    break
                if datetime.now(UTC) - scheduled_at > timedelta(minutes=15):
                    continue
                current = self.preferences.get(preference.user_id)
                if current != preference or not current.enabled:
                    continue
                try:
                    await self.digest.deliver(bot, preference.user_id, scheduled=True,
                                              now=scheduled_at)
                except Exception as error:
                    logger.warning(json.dumps({"event": "digest_schedule_failure",
                                               "error_type": type(error).__name__}))
