import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cyberpath_ai.ai.explainer import ExplainError, FIELDS
from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.news import format_explanation
from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.service import NewsResult


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
ITEM = NewsItem("BleepingComputer", "<headline>", "<source>", NOW,
                "https://example.com/article?a=1&b=2")
BODY = {field: "<b>AI</b> & text" for field in FIELDS if field != "terms"}
BODY["terms"] = [{"term": "<CVE>", "meaning": "& issue"},
                 {"term": "patch", "meaning": "update"}]


def update(number: int, data: str, user_id: int = 42) -> Update:
    message = Message(message_id=10, date=NOW, chat=Chat(id=42, type="private"),
                      from_user=User(id=100, is_bot=True, first_name="Bot"), text="News")
    return Update(update_id=number, callback_query=CallbackQuery(
        id=str(number), from_user=User(id=user_id, is_bot=False, first_name="User"),
        chat_instance="test", data=data, message=message))


def test_explanation_html_is_escaped_and_shortened() -> None:
    text = format_explanation(ITEM, BODY, "beginner")
    assert "&lt;headline&gt;" in text
    assert "&lt;b&gt;AI&lt;/b&gt; &amp; text" in text
    assert "a=1&amp;b=2" in text
    assert "гипотетический" in text
    assert len(text) < 3900
    long = {field: "<&>" * 2000 for field in FIELDS if field != "terms"}
    long["terms"] = BODY["terms"]
    shortened = format_explanation(ITEM, long, "advanced")
    assert len(shortened) < 3900
    assert "сокращено" in shortened


def test_explain_levels_return_navigation_outdated_and_allowlist(tmp_path: Path) -> None:
    async def exercise() -> None:
        news = AsyncMock()
        news.get_news.return_value = NewsResult((ITEM,))
        ai = AsyncMock()
        ai.explain.return_value = BODY
        dispatcher = create_dispatcher(
            Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "db.sqlite"),
            news_service=news, ai_explainer=ai,
        )
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, update(1, "menu:news"))
            edits = [c.args[1] for c in bot.session.make_request.await_args_list
                     if isinstance(c.args[1], EditMessageText)]
            article = edits[-1]
            button = article.reply_markup.inline_keyboard[3][0]
            assert button.text == "🧠 Объяснить новость"
            await dispatcher.feed_update(bot, update(2, button.callback_data))
            edits = [c.args[1] for c in bot.session.make_request.await_args_list
                     if isinstance(c.args[1], EditMessageText)]
            assert any("Готовлю объяснение" in edit.text for edit in edits)
            explained = edits[-1]
            assert "Что произошло?" in explained.text
            assert explained.link_preview_options.is_disabled is True
            ai.explain.assert_awaited_once_with(ITEM, "beginner")
            intermediate = explained.reply_markup.inline_keyboard[0][1].callback_data
            await dispatcher.feed_update(bot, update(3, intermediate))
            ai.explain.assert_awaited_with(ITEM, "intermediate")
            before = ai.explain.await_count
            await dispatcher.feed_update(bot, update(4, button.callback_data, user_id=99))
            assert ai.explain.await_count == before
            back = explained.reply_markup.inline_keyboard[1][0].callback_data
            await dispatcher.feed_update(bot, update(5, back))
            edits = [c.args[1] for c in bot.session.make_request.await_args_list
                     if isinstance(c.args[1], EditMessageText)]
            assert "Новости · 1/1" in edits[-1].text
            await dispatcher.feed_update(bot, update(6, back))
            assert "outdated" in bot.session.make_request.await_args.args[1].text
    asyncio.run(exercise())


def test_failed_explanation_can_retry_without_losing_article(tmp_path: Path) -> None:
    async def exercise() -> None:
        news = AsyncMock()
        news.get_news.return_value = NewsResult((ITEM,))
        ai = AsyncMock()
        ai.explain.side_effect = [ExplainError("Temporary failure"), BODY]
        dispatcher = create_dispatcher(
            Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "db.sqlite"),
            news_service=news, ai_explainer=ai,
        )
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, update(1, "menu:news"))
            article = [c.args[1] for c in bot.session.make_request.await_args_list
                       if isinstance(c.args[1], EditMessageText)][-1]
            explain = article.reply_markup.inline_keyboard[3][0].callback_data
            await dispatcher.feed_update(bot, update(2, explain))
            failed = [c.args[1] for c in bot.session.make_request.await_args_list
                      if isinstance(c.args[1], EditMessageText)][-1]
            assert "Temporary failure" in failed.text
            retry = failed.reply_markup.inline_keyboard[0][0].callback_data
            await dispatcher.feed_update(bot, update(3, retry))
            assert ai.explain.await_count == 2
            completed = [c.args[1] for c in bot.session.make_request.await_args_list
                         if isinstance(c.args[1], EditMessageText)][-1]
            assert "Что произошло?" in completed.text
    asyncio.run(exercise())
