import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.types import Chat, Message, MessageEntity, Update, User

from cyberpath_ai.access import AllowlistMiddleware
from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.scheduler import PreferenceStore


def test_allowlist_passes_only_configured_user() -> None:
    middleware = AllowlistMiddleware(frozenset({42}))
    handler = AsyncMock(return_value="handled")
    allowed_event = SimpleNamespace(event=SimpleNamespace(from_user=SimpleNamespace(id=42)))
    denied_event = SimpleNamespace(event=SimpleNamespace(from_user=SimpleNamespace(id=99)))

    assert asyncio.run(middleware(handler, allowed_event, {})) == "handled"
    assert asyncio.run(middleware(handler, denied_event, {})) is None
    handler.assert_awaited_once()


def test_allowlist_rejects_updates_without_sender() -> None:
    middleware = AllowlistMiddleware(frozenset({42}))
    handler = AsyncMock()
    event = SimpleNamespace(event=SimpleNamespace())

    assert asyncio.run(middleware(handler, event, {})) is None
    handler.assert_not_awaited()


def test_dispatcher_handles_allowed_start_and_ignores_other_user(tmp_path) -> None:
    async def exercise() -> None:
        settings = Settings("123456:TEST", frozenset({42}),
                            news_db_path=tmp_path / "access.sqlite")
        dispatcher = create_dispatcher(settings)
        async with Bot(token=settings.telegram_bot_token) as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            for update_id, user_id in enumerate((42, 99), start=1):
                user = User(id=user_id, is_bot=False, first_name="Test")
                message = Message(
                    message_id=update_id,
                    date=datetime.now(UTC),
                    chat=Chat(id=user_id, type="private"),
                    from_user=user,
                    text="/start",
                    entities=[MessageEntity(type="bot_command", offset=0, length=6)],
                )
                await dispatcher.feed_update(bot, Update(update_id=update_id, message=message))
            bot.session.make_request.assert_awaited_once()
            assert PreferenceStore(settings.news_db_path).get(42).chat_id == 42
            assert PreferenceStore(settings.news_db_path).get(99) is None

    asyncio.run(exercise())
