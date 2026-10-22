"""Shared Telegram navigation controls; navigation never changes persisted progress."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


HOME_DATA = "nav:home"
HOME_LABEL = "🏠 Главное меню"
BACK_LABEL = "◀️ Назад"


def home_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text=HOME_LABEL, callback_data=HOME_DATA)


def back_button(callback_data: str, destination: str = "") -> InlineKeyboardButton:
    label = BACK_LABEL + (f" · {destination}" if destination else "")
    return InlineKeyboardButton(text=label, callback_data=callback_data)


def add_navigation(keyboard: InlineKeyboardMarkup, *,
                   back_data: str | None = None, back_to: str = "") -> InlineKeyboardMarkup:
    rows = [list(row) for row in keyboard.inline_keyboard]
    if back_data:
        rows.append([back_button(back_data, back_to)])
    rows.append([home_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)
