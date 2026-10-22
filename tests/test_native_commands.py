import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage, SetChatMenuButton, SetMyCommands
from aiogram.types import Chat, Message, Update, User

from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.commands import COMMANDS, register_native_menu
from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.service import NewsResult


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def command(number: int, text: str, user_id: int = 42) -> Update:
    return Update(update_id=number, message=Message(
        message_id=number, date=NOW, chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Learner"), text=text))


def sent(bot: Bot) -> list[SendMessage]:
    return [call.args[1] for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], SendMessage)]


def test_native_menu_registers_expected_commands_and_handles_api_errors(caplog):
    async def exercise():
        bot = SimpleNamespace(set_my_commands=AsyncMock(), set_chat_menu_button=AsyncMock())
        await register_native_menu(bot)
        assert [item.command for item in bot.set_my_commands.await_args.args[0]] == [
            "start", "menu", "news", "learn", "quiz", "mission", "review",
            "progress", "settings", "help"]
        assert bot.set_chat_menu_button.await_args.kwargs["menu_button"].type == "commands"
        bot.set_my_commands.side_effect = TelegramBadRequest(
            SetMyCommands(commands=list(COMMANDS)), "private token detail")
        await register_native_menu(bot)
        assert bot.set_chat_menu_button.await_count == 2
        bot.set_chat_menu_button.side_effect = TelegramBadRequest(
            SetChatMenuButton(), "another private detail")
        await register_native_menu(bot)
    asyncio.run(exercise())
    assert "private token detail" not in caplog.text
    assert "another private detail" not in caplog.text


def test_all_commands_open_real_sections_and_allowlist(tmp_path: Path):
    async def exercise():
        item = NewsItem("BleepingComputer", "Real headline", "Source description", NOW,
                        "https://example.org/real")
        news = SimpleNamespace(get_news=AsyncMock(return_value=NewsResult((item,))))
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "db.sqlite"),
                                       news_service=news)
        async with Bot(token="123456:TEST") as bot:
            next_id = [100]
            async def response(_bot, method, **_kwargs):
                if isinstance(method, SendMessage):
                    next_id[0] += 1
                    return Message(message_id=next_id[0], date=NOW,
                                   chat=Chat(id=42, type="private"), text=method.text)
                return True
            bot.session.make_request = AsyncMock(side_effect=response)
            names = ["/start", "/menu", "/news", "/learn", "/quiz", "/mission",
                     "/review", "/progress", "/settings", "/help"]
            expected = ["CyberPath", "CyberPath", "Real headline", "Academy",
                        "квиза", "Миссия дня", "Повторение", "Ваш прогресс",
                        "Настройки", "Помощь"]
            for index, (name, phrase) in enumerate(zip(names, expected), 1):
                await dispatcher.feed_update(bot, command(index, name))
                assert phrase in sent(bot)[-1].text
            assert dispatcher["mission_store"].get(42) is not None
            assert news.get_news.await_count == 2  # /news and initial /mission.
            before = bot.session.make_request.await_count
            await dispatcher.feed_update(bot, command(20, "/help", user_id=99))
            assert bot.session.make_request.await_count == before
    asyncio.run(exercise())


def test_command_exits_settings_time_input_without_changing_hour(tmp_path: Path):
    async def exercise():
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "db.sqlite"))
        async with Bot(token="123456:TEST") as bot:
            async def response(_bot, method, **_kwargs):
                if isinstance(method, SendMessage):
                    return Message(message_id=50, date=NOW, chat=Chat(id=42, type="private"),
                                   text=method.text)
                return True
            bot.session.make_request = AsyncMock(side_effect=response)
            await dispatcher.feed_update(bot, command(1, "/settings"))
            from aiogram.types import CallbackQuery
            previous = Message(message_id=50, date=NOW, chat=Chat(id=42, type="private"),
                               from_user=User(id=100, is_bot=True, first_name="Bot"), text="Settings")
            query = CallbackQuery(id="2", from_user=User(id=42, is_bot=False,
                                  first_name="Learner"), chat_instance="test",
                                  data="settings:time", message=previous)
            await dispatcher.feed_update(bot, Update(update_id=2, callback_query=query))
            await dispatcher.feed_update(bot, command(3, "/help"))
            assert "Помощь" in sent(bot)[-1].text
            await dispatcher.feed_update(bot, command(4, "18"))
            assert dispatcher["preferences"].get(42).hour == 9
    asyncio.run(exercise())
