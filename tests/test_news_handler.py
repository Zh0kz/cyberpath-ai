import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.news import format_news
from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.service import NewsResult


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def sample_item() -> NewsItem:
    return NewsItem(
        "BleepingComputer", "Source headline", "Source description", NOW,
        "https://www.bleepingcomputer.com/news/security/story/",
    )


def test_format_news_loading_outcomes() -> None:
    assert "недоступны" in format_news(NewsResult((), ("NVD",)))
    assert "не найдено новостей с подтверждённым источником" in format_news(NewsResult(()))
    shown = format_news(NewsResult((sample_item(),), from_cache=True))
    assert "Source headline" in shown
    assert "BleepingComputer · 2026-10-21 12:00 UTC" in shown
    assert "https://www.bleepingcomputer.com/news/security/story/" in shown
    assert "сохранённых публикаций" in shown


def test_news_callback_and_refresh_are_allowlisted(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "news.sqlite3")
        service = AsyncMock()
        service.get_news.return_value = NewsResult((sample_item(),))
        dispatcher = create_dispatcher(settings, news_service=service)
        async with Bot(token=settings.telegram_bot_token) as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            for update_id, (user_id, data) in enumerate(
                ((42, "menu:news"), (42, "news:refresh"), (99, "menu:news")), start=1
            ):
                user = User(id=user_id, is_bot=False, first_name="Test")
                message = Message(
                    message_id=update_id, date=NOW,
                    chat=Chat(id=user_id, type="private"),
                    from_user=User(id=100, is_bot=True, first_name="CyberPath"),
                    text="Menu",
                )
                callback = CallbackQuery(
                    id=str(update_id), from_user=user, chat_instance="test",
                    data=data, message=message,
                )
                await dispatcher.feed_update(bot, Update(update_id=update_id, callback_query=callback))
            assert service.get_news.await_count == 2
            assert service.get_news.await_args_list[0].kwargs == {"refresh": False}
            assert service.get_news.await_args_list[1].kwargs == {"refresh": True}
            edits = [call.args[1] for call in bot.session.make_request.await_args_list
                     if isinstance(call.args[1], EditMessageText)]
            assert any("Loading cybersecurity news" in edit.text for edit in edits)
            assert any("Source headline" in edit.text for edit in edits)
            assert any(edit.reply_markup and edit.reply_markup.inline_keyboard[0][0].text == "Обновить новости"
                       for edit in edits)

    asyncio.run(exercise())
