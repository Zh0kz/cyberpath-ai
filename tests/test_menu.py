import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cyberpath_ai.handlers.menu import main_menu, menu_selection, start


def test_main_menu_has_expected_buttons() -> None:
    keyboard = main_menu()

    assert [[(item.text, item.callback_data) for item in row]
            for row in keyboard.inline_keyboard] == [
        [("📰 Новости", "menu:news"), ("📚 Обучение", "menu:learn")],
        [("🧠 Тесты", "menu:quiz"), ("🎯 Миссия", "menu:mission")],
        [("🔁 Повторение", "menu:review"), ("📊 Прогресс", "menu:progress")],
        [("⚙️ Настройки", "menu:settings"), ("❓ Помощь", "menu:help")],
    ]


def test_start_sends_welcome_with_menu() -> None:
    message = SimpleNamespace(answer=AsyncMock())

    asyncio.run(start(message))

    message.answer.assert_awaited_once()
    args, kwargs = message.answer.await_args
    assert "CyberPath AI" in args[0]
    assert kwargs["reply_markup"] == main_menu()


def test_menu_callback_acknowledges_section() -> None:
    query = SimpleNamespace(data="menu:learn", answer=AsyncMock())

    asyncio.run(menu_selection(query))

    query.answer.assert_awaited_once_with("Откройте раздел через /menu.", show_alert=True)


def test_unknown_menu_callback_does_not_open_section() -> None:
    query = SimpleNamespace(data="menu:invalid", answer=AsyncMock())

    asyncio.run(menu_selection(query))

    query.answer.assert_awaited_once_with("Unknown menu item.", show_alert=True)
