import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.menu import render_dashboard
from cyberpath_ai.handlers.ui import safe, screen


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def callback(number: int, data: str, user_id: int = 42) -> Update:
    message = Message(message_id=10, date=NOW, chat=Chat(id=user_id, type="private"),
                      from_user=User(id=100, is_bot=True, first_name="Bot"), text="Panel")
    return Update(update_id=number, callback_query=CallbackQuery(
        id=str(number), from_user=User(id=user_id, is_bot=False, first_name="Learner"),
        chat_instance="test", data=data, message=message))


def user_text(number: int, text: str) -> Update:
    return Update(update_id=number, message=Message(
        message_id=number, date=NOW, chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Learner"), text=text))


def last_edit(bot: Bot) -> EditMessageText:
    return next(call.args[1] for call in reversed(bot.session.make_request.await_args_list)
                if isinstance(call.args[1], EditMessageText))


def button(method: EditMessageText, phrase: str) -> str:
    return next(item.callback_data for row in method.reply_markup.inline_keyboard for item in row
                if phrase in item.text)


def test_dashboard_uses_persisted_progress_and_has_empty_state(tmp_path: Path):
    dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42, 99}),
                                            news_db_path=tmp_path / "db.sqlite"))
    catalog = dispatcher["catalog"]
    academy = dispatcher["academy_store"]
    adaptive = dispatcher["adaptive_store"]
    missions = dispatcher["mission_store"]
    empty, keyboard = render_dashboard(42, academy, adaptive, missions, catalog)
    assert "0/9" in empty and "ещё не открыта" in empty
    assert "Начните с короткого урока" in empty
    assert "0%" not in empty
    assert len(keyboard.inline_keyboard) == 4

    lesson = catalog.modules[0].lessons[0]
    academy.view_section(42, lesson, len(lesson.sections) + 1)
    academy.start_attempt(42, lesson)
    with adaptive._connect() as db:
        adaptive._record(db, 42, lesson.questions[0]["id"], "quiz:dashboard-seed",
                         "quiz", 0, 0, False, datetime.now(UTC) - timedelta(days=2))
    missions.create_today(42)
    filled, keyboard = render_dashboard(42, academy, adaptive, missions, catalog)
    assert "1/9" in filled and "Повторение: 1" in filled
    assert "Миссия сегодня: 0/" in filled
    assert any(item.callback_data == "dash:continue:quiz"
               for row in keyboard.inline_keyboard for item in row)
    other, _ = render_dashboard(99, academy, adaptive, missions, catalog)
    assert "0/9" in other and "ещё не открыта" in other


def test_continue_quiz_callback_uses_existing_attempt_and_home_preserves_it(tmp_path: Path):
    async def exercise():
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "db.sqlite"))
        academy = dispatcher["academy_store"]
        lesson = dispatcher["catalog"].modules[0].lessons[0]
        attempt_id = academy.start_attempt(42, lesson)
        academy.ensure_run(42, attempt_id, 0)
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "dash:continue:quiz"))
            assert "Вопрос 1/5" in last_edit(bot).text
            await dispatcher.feed_update(bot, callback(2, "nav:home"))
            assert academy.active_attempt(42)["id"] == attempt_id
            assert "Продолжить квиз" in str(last_edit(bot).reply_markup)
            await dispatcher.feed_update(bot, callback(3, "dash:continue:quiz"))
            assert academy.active_attempt(42)["id"] == attempt_id
            assert "Вопрос 1/5" in last_edit(bot).text
    asyncio.run(exercise())


def test_help_callback_has_navigation_and_falls_back_on_edit_error(tmp_path: Path):
    async def exercise():
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "db.sqlite"))
        async with Bot(token="123456:TEST") as bot:
            response = Message(message_id=20, date=NOW, chat=Chat(id=42, type="private"),
                               text="Help")
            async def request(_bot, method, **_kwargs):
                if isinstance(method, EditMessageText):
                    raise TelegramBadRequest(method, "cannot edit")
                return response if isinstance(method, SendMessage) else True
            bot.session.make_request = AsyncMock(side_effect=request)
            await dispatcher.feed_update(bot, callback(1, "menu:help"))
            sends = [call.args[1] for call in bot.session.make_request.await_args_list
                     if isinstance(call.args[1], SendMessage)]
            assert "Начните с урока" in sends[-1].text
            assert "Подсказок до трёх" in sends[-1].text
            assert any(item.callback_data == "nav:home"
                       for row in sends[-1].reply_markup.inline_keyboard for item in row)
            assert len(sends[-1].text) < 4096
    asyncio.run(exercise())


def test_command_during_quiz_text_answer_is_not_submitted(tmp_path: Path):
    async def exercise():
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "db.sqlite"))
        academy = dispatcher["academy_store"]
        lesson = dispatcher["catalog"].modules[0].lessons[0]
        attempt_id = academy.start_attempt(42, lesson)
        with academy._connect() as db:
            db.execute("UPDATE quiz_attempts SET current_index = 2 WHERE id = ?", (attempt_id,))
        run = academy.ensure_run(42, attempt_id, 2)
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "dash:continue:quiz"))
            assert "Вопрос 3/5" in last_edit(bot).text
            await dispatcher.feed_update(bot, user_text(2, "/help"))
            assert academy.current_run(42, attempt_id, 2)["submitted_answer"] is None
            assert academy.current_run(42, attempt_id, 2)["id"] == run["id"]
            await dispatcher.feed_update(bot, user_text(3, "/quiz"))
            assert academy.active_attempt(42)["id"] == attempt_id
    asyncio.run(exercise())


def test_shared_html_helpers_escape_and_limit_untrusted_text():
    hostile = "<&>" * 2000
    escaped = safe(hostile, 200)
    assert len(escaped) <= 201
    assert "<" not in escaped and "&lt;" in escaped
    text = screen("News <unsafe>", "Status & details", escaped)
    assert "News &lt;unsafe&gt;" in text and "Status &amp; details" in text
    assert len(text) < 4096
