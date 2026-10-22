import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.news import NewsView, format_news, news_keyboard
from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.service import NewsResult


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def make_item(number: int) -> NewsItem:
    return NewsItem(
        "BleepingComputer",
        f"Headline {number} <unsafe>",
        "A <b>source</b> says & reports an issue.",
        NOW,
        f"https://www.bleepingcomputer.com/news/security/story-{number}/?a=1&b=2",
    )


def make_callback(update_id: int, data: str, *, user_id: int = 42, message_id: int = 10) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Test")
    message = Message(
        message_id=message_id, date=NOW, chat=Chat(id=user_id, type="private"),
        from_user=User(id=100, is_bot=True, first_name="CyberPath"), text="News",
    )
    return Update(update_id=update_id, callback_query=CallbackQuery(
        id=str(update_id), from_user=user, chat_instance="test",
        data=data, message=message,
    ))


def test_one_article_html_escaping_and_source_button() -> None:
    result = NewsResult((make_item(1), make_item(2)), cooldown_seconds=42)
    text = format_news(result)
    keyboard = news_keyboard(NewsView("abcdef12", result, 0, NOW))

    assert "1/2" in text
    assert "Headline 1 &lt;unsafe&gt;" in text
    assert "Headline 2" not in text
    assert "&lt;b&gt;source&lt;/b&gt;" in text
    assert "a=1&amp;b=2" in text
    assert "Refresh cooldown: try again in 42s" in text
    assert keyboard.inline_keyboard[1][0].text == "◀ Назад"
    assert keyboard.inline_keyboard[1][1].text == "Далее ▶"
    assert keyboard.inline_keyboard[2][0].url == make_item(1).source_url


def test_navigation_boundaries_outdated_buttons_refresh_and_allowlist(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = AsyncMock()
        service.get_news.return_value = NewsResult((make_item(1), make_item(2)))
        dispatcher = create_dispatcher(
            Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "news.sqlite3"),
            news_service=service,
        )
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, make_callback(1, "menu:news"))
            edits = [call.args[1] for call in bot.session.make_request.await_args_list
                     if isinstance(call.args[1], EditMessageText)]
            first = edits[-1]
            assert first.parse_mode == "HTML"
            assert first.link_preview_options.is_disabled is True
            assert "1/2" in first.text
            previous = first.reply_markup.inline_keyboard[1][0].callback_data
            next_button = first.reply_markup.inline_keyboard[1][1].callback_data
            old_refresh = first.reply_markup.inline_keyboard[0][0].callback_data

            before = bot.session.make_request.await_count
            await dispatcher.feed_update(bot, make_callback(2, previous))
            assert bot.session.make_request.await_count == before + 1
            assert "Already at the first" in bot.session.make_request.await_args.args[1].text

            await dispatcher.feed_update(bot, make_callback(3, next_button))
            edits = [call.args[1] for call in bot.session.make_request.await_args_list
                     if isinstance(call.args[1], EditMessageText)]
            second = edits[-1]
            assert "2/2" in second.text
            assert "Headline 2" in second.text
            assert service.get_news.await_count == 1

            await dispatcher.feed_update(bot, make_callback(4, next_button))
            assert "outdated" in bot.session.make_request.await_args.args[1].text
            last_next = second.reply_markup.inline_keyboard[1][1].callback_data
            await dispatcher.feed_update(bot, make_callback(5, last_next))
            assert "Already at the last" in bot.session.make_request.await_args.args[1].text

            last_previous = second.reply_markup.inline_keyboard[1][0].callback_data
            await dispatcher.feed_update(bot, make_callback(9, last_previous))
            edits = [call.args[1] for call in bot.session.make_request.await_args_list
                     if isinstance(call.args[1], EditMessageText)]
            assert "1/2" in edits[-1].text

            await dispatcher.feed_update(bot, make_callback(6, old_refresh))
            assert service.get_news.await_count == 2
            await dispatcher.feed_update(bot, make_callback(7, next_button))
            assert "expired" in bot.session.make_request.await_args.args[1].text

            prior_calls = bot.session.make_request.await_count
            await dispatcher.feed_update(bot, make_callback(8, "menu:news", user_id=99))
            assert bot.session.make_request.await_count == prior_calls
            assert all(
                method.link_preview_options.is_disabled is True
                for call in bot.session.make_request.await_args_list
                if isinstance((method := call.args[1]), EditMessageText)
            )
            assert any(isinstance(call.args[1], AnswerCallbackQuery)
                       for call in bot.session.make_request.await_args_list)

    asyncio.run(exercise())


def test_empty_view_refresh_button_expires_after_new_view(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = AsyncMock()
        service.get_news.return_value = NewsResult(())
        dispatcher = create_dispatcher(
            Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "news.sqlite3"),
            news_service=service,
        )
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, make_callback(1, "menu:news"))
            edits = [call.args[1] for call in bot.session.make_request.await_args_list
                     if isinstance(call.args[1], EditMessageText)]
            first_refresh = edits[-1].reply_markup.inline_keyboard[0][0].callback_data
            await dispatcher.feed_update(bot, make_callback(2, first_refresh))
            assert service.get_news.await_count == 2
            await dispatcher.feed_update(bot, make_callback(3, first_refresh))
            assert "expired" in bot.session.make_request.await_args.args[1].text

    asyncio.run(exercise())


def test_edit_failure_sends_replacement_message(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = AsyncMock()
        service.get_news.return_value = NewsResult((make_item(1),))
        dispatcher = create_dispatcher(
            Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "news.sqlite3"),
            news_service=service,
        )
        failed_once = False
        calls = []

        async def make_request(bot, method, **_):
            nonlocal failed_once
            calls.append(method)
            if isinstance(method, EditMessageText) and not failed_once:
                failed_once = True
                raise TelegramBadRequest(method, "message to edit not found")
            if isinstance(method, SendMessage):
                return Message(
                    message_id=20, date=NOW, chat=Chat(id=42, type="private"),
                    from_user=User(id=100, is_bot=True, first_name="CyberPath"),
                    text=method.text,
                ).as_(bot)
            return True

        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(side_effect=make_request)
            await dispatcher.feed_update(bot, make_callback(1, "menu:news"))
        assert any(isinstance(method, SendMessage) for method in calls)
        assert any(isinstance(method, EditMessageText) and "1/1" in method.text
                   for method in calls)
        service.get_news.assert_awaited_once()

    asyncio.run(exercise())
