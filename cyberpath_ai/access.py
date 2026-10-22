"""Reject all Telegram updates that have no configured sender."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update


class AllowlistMiddleware(BaseMiddleware):
    def __init__(self, allowed_user_ids: frozenset[int],
                 capture_private_chat: Callable[[int, int], None] | None = None) -> None:
        self.allowed_user_ids = allowed_user_ids
        self.capture_private_chat = capture_private_chat

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        sender = getattr(event.event, "from_user", None)
        if sender is None or sender.id not in self.allowed_user_ids:
            return None
        message = event.event if isinstance(event.event, Message) else getattr(event.event, "message", None)
        if (self.capture_private_chat is not None and isinstance(message, Message)
                and message.chat.type == "private"):
            self.capture_private_chat(sender.id, message.chat.id)
        return await handler(event, data)
