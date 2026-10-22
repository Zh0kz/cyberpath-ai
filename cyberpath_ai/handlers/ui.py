"""Small, reusable Telegram HTML and inline-layout helpers."""

from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


TELEGRAM_TEXT_LIMIT = 4096
SAFE_TEXT_LIMIT = 3900


def safe(value: object, limit: int = 1200) -> str:
    """Escape untrusted text before truncation so HTML entities remain intact."""
    output = ""
    for character in str(value):
        encoded = escape(character)
        if len(output) + len(encoded) > limit:
            return output.rstrip() + "…"
        output += encoded
    return output


def screen(title: str, subtitle: str = "", *trusted_blocks: str) -> str:
    """Compose a short Telegram screen; blocks must already be safe HTML."""
    parts = [f"<b>{safe(title, 180)}</b>"]
    if subtitle:
        parts.append(safe(subtitle, 350))
    parts.extend(block for block in trusted_blocks if block)
    return "\n\n".join(parts)


def indicator(done: bool, label: str) -> str:
    return ("✓ " if done else "· ") + safe(label, 250)


def paired_keyboard(*pairs: tuple[tuple[str, str], ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in pair]
        for pair in pairs
    ])
