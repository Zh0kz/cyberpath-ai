import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.service import NewsResult


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def callback(number: int, data: str, user_id: int = 42) -> Update:
    message = Message(message_id=10, date=NOW, chat=Chat(id=user_id, type="private"),
                      from_user=User(id=100, is_bot=True, first_name="Bot"), text="Panel")
    return Update(update_id=number, callback_query=CallbackQuery(
        id=str(number), from_user=User(id=user_id, is_bot=False, first_name="Student"),
        chat_instance="test", data=data, message=message))


def message(number: int, text: str, user_id: int = 42) -> Update:
    return Update(update_id=number, message=Message(
        message_id=number, date=NOW, chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Student"), text=text))


def edits(bot: Bot):
    return [call.args[1] for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], EditMessageText)]


def button(method: EditMessageText, phrase: str) -> str:
    return next(item.callback_data for row in method.reply_markup.inline_keyboard for item in row
                if phrase in item.text)


def test_academy_home_back_and_quiz_interruption(tmp_path: Path):
    async def exercise():
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "db.sqlite3"))
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "menu:learn"))
            assert button(edits(bot)[-1], "Главное меню") == "nav:home"
            await dispatcher.feed_update(bot, callback(2, button(edits(bot)[-1], "Networking Fundamentals")))
            await dispatcher.feed_update(bot, callback(3, button(edits(bot)[-1], "IP Addressing")))
            await dispatcher.feed_update(bot, callback(4, button(edits(bot)[-1], "Следующий раздел")))
            assert dispatcher["academy_store"].last_studied(42) == ("ip_subnets", 1)
            stale_next = button(edits(bot)[-1], "Следующий раздел")
            await dispatcher.feed_update(bot, callback(5, "nav:home"))
            assert "CyberPath AI" in edits(bot)[-1].text
            assert dispatcher["academy_store"].last_studied(42) == ("ip_subnets", 1)
            before = len(edits(bot))
            await dispatcher.feed_update(bot, callback(6, stale_next))
            assert len(edits(bot)) == before
            assert "outdated" in bot.session.make_request.await_args.args[1].text
            await dispatcher.feed_update(bot, callback(7, "menu:learn"))
            await dispatcher.feed_update(bot, callback(8, button(edits(bot)[-1], "Продолжить обучение")))
            assert "1/3" in edits(bot)[-1].text
            await dispatcher.feed_update(bot, callback(9, button(edits(bot)[-1], "Назад")))
            assert "Выберите урок" in edits(bot)[-1].text

            await dispatcher.feed_update(bot, callback(10, "menu:quiz"))
            await dispatcher.feed_update(bot, callback(11, button(edits(bot)[-1], "Networking Fundamentals")))
            await dispatcher.feed_update(bot, callback(12, button(edits(bot)[-1], "IP Addressing")))
            await dispatcher.feed_update(bot, callback(13, button(edits(bot)[-1], "Начать новый")))
            await dispatcher.feed_update(bot, callback(14, button(edits(bot)[-1], "Подсказка")))
            attempt = dispatcher["academy_store"].active_attempt(42)
            assert dispatcher["academy_store"].current_run(42, attempt["id"], 0)["hints_used"] == 1
            await dispatcher.feed_update(bot, message(15, "/menu"))
            assert any(isinstance(call.args[1], SendMessage) and "CyberPath AI" in call.args[1].text
                       for call in bot.session.make_request.await_args_list)
            assert dispatcher["academy_store"].active_attempt(42)["id"] == attempt["id"]
            await dispatcher.feed_update(bot, callback(16, "menu:quiz"))
            assert any("Продолжить квиз" in item.text
                       for row in edits(bot)[-1].reply_markup.inline_keyboard for item in row)
            await dispatcher.feed_update(bot, callback(17, button(edits(bot)[-1], "Продолжить квиз")))
            assert "Подсказки" in edits(bot)[-1].text
            denied = bot.session.make_request.await_count
            await dispatcher.feed_update(bot, callback(18, "nav:home", user_id=99))
            assert bot.session.make_request.await_count == denied

    asyncio.run(exercise())


def test_news_home_invalidates_old_article_and_settings_input(tmp_path: Path):
    async def exercise():
        news = AsyncMock()
        news.get_news.return_value = NewsResult((NewsItem(
            "BleepingComputer", "A real headline", "Description", NOW,
            "https://example.org/news/1"),))
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "db.sqlite3"),
                                       news_service=news)
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "menu:news"))
            assert button(edits(bot)[-1], "Главное меню") == "nav:home"
            old_refresh = button(edits(bot)[-1], "Обновить новости")
            await dispatcher.feed_update(bot, callback(2, "nav:home"))
            await dispatcher.feed_update(bot, callback(3, old_refresh))
            assert isinstance(bot.session.make_request.await_args.args[1], AnswerCallbackQuery)
            assert "expired" in bot.session.make_request.await_args.args[1].text
            await dispatcher.feed_update(bot, callback(4, "menu:settings"))
            assert button(edits(bot)[-1], "Главное меню") == "nav:home"
            await dispatcher.feed_update(bot, callback(5, "settings:time"))
            await dispatcher.feed_update(bot, message(6, "/menu"))
            pref = dispatcher["preferences"].get(42)
            assert pref.hour == 9
            await dispatcher.feed_update(bot, message(7, "18"))
            assert dispatcher["preferences"].get(42).hour == 9
            await dispatcher.feed_update(bot, callback(8, "settings:enable"))
            assert "outdated" in bot.session.make_request.await_args.args[1].text
            assert not dispatcher["preferences"].get(42).enabled

    asyncio.run(exercise())
