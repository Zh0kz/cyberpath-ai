import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cyberpath_ai.academy.content import Catalog, load_catalog
from cyberpath_ai.academy.storage import AcademyStore
from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.academy import AcademyViews, _new, _show, render


NOW = datetime(2026, 10, 21, tzinfo=UTC)


def callback(update_id: int, data: str, user_id: int = 42, message_id: int = 10) -> Update:
    return Update(update_id=update_id, callback_query=CallbackQuery(
        id=str(update_id), from_user=User(id=user_id, is_bot=False, first_name="Student"),
        chat_instance="academy", data=data,
        message=Message(message_id=message_id, date=NOW,
                        chat=Chat(id=user_id, type="private"),
                        from_user=User(id=100, is_bot=True, first_name="CyberPath"), text="Menu")))


def user_text(update_id: int, text: str, user_id: int = 42) -> Update:
    return Update(update_id=update_id, message=Message(
        message_id=update_id, date=NOW, chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Student"), text=text))


def edits(bot: Bot):
    return [call.args[1] for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], EditMessageText)]


def button(method: EditMessageText, label: str) -> str:
    return next(item.callback_data for row in method.reply_markup.inline_keyboard for item in row
                if label in item.text)


def test_learn_navigation_resume_quiz_hints_show_progress_and_allowlist(tmp_path: Path):
    async def exercise():
        settings = Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "bot.sqlite3",
                            openrouter_api_key="")
        dispatcher = create_dispatcher(settings)
        async with Bot(token=settings.telegram_bot_token) as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "menu:learn"))
            assert "CyberPath Academy" in edits(bot)[-1].text
            module = button(edits(bot)[-1], "Networking Fundamentals")
            await dispatcher.feed_update(bot, callback(2, module))
            lesson = button(edits(bot)[-1], "IP Addressing and Subnets")
            await dispatcher.feed_update(bot, callback(3, lesson))
            assert "Цели" in edits(bot)[-1].text
            first_next = button(edits(bot)[-1], "Следующий раздел")
            await dispatcher.feed_update(bot, callback(4, first_next))
            assert "1/3" in edits(bot)[-1].text
            before = len(edits(bot))
            await dispatcher.feed_update(bot, callback(5, first_next))
            assert len(edits(bot)) == before
            assert isinstance(bot.session.make_request.await_args.args[1], AnswerCallbackQuery)
            assert "outdated" in bot.session.make_request.await_args.args[1].text

            # A new menu opens a new view; Continue restores the section from SQLite.
            await dispatcher.feed_update(bot, callback(6, "menu:learn"))
            await dispatcher.feed_update(bot, callback(7, button(edits(bot)[-1], "Продолжить обучение")))
            assert "1/3" in edits(bot)[-1].text
            for update_id in range(8, 11):
                await dispatcher.feed_update(bot, callback(update_id, button(edits(bot)[-1], "Следующий раздел")))
            assert "Итог" in edits(bot)[-1].text
            assert dispatcher["academy_store"].lesson_progress(42, "ip_subnets")["completed_at_utc"]
            await dispatcher.feed_update(bot, callback(11, button(edits(bot)[-1], "Начать квиз")))
            assert "Вопрос 1/5" in edits(bot)[-1].text
            assert edits(bot)[-1].link_preview_options.is_disabled
            assert dispatcher["academy_store"].active_attempt(42)
            for i in range(3):
                await dispatcher.feed_update(bot, callback(12 + i, button(edits(bot)[-1], "Подсказка")))
            assert any("Показать ответ" in item.text
                       for row in edits(bot)[-1].reply_markup.inline_keyboard for item in row)
            await dispatcher.feed_update(bot, callback(15, button(edits(bot)[-1], "Показать ответ")))
            assert "0.00/1" in edits(bot)[-1].text
            store = dispatcher["academy_store"]
            attempt = store.active_attempt(42)
            assert store.current_run(42, attempt["id"], 0)["revealed"] == 1
            await dispatcher.feed_update(bot, callback(16, button(edits(bot)[-1], "Повторить вопрос")))
            assert "Вопрос 1/5" in edits(bot)[-1].text
            # The button labels are plain Telegram text, while lesson HTML stays escaped.
            await dispatcher.feed_update(bot, callback(17, button(edits(bot)[-1], "24 бита")))
            assert "1.00/1" in edits(bot)[-1].text
            await dispatcher.feed_update(bot, callback(18, button(edits(bot)[-1], "Следующий вопрос")))
            assert "Вопрос 2/5" in edits(bot)[-1].text
            denied_count = bot.session.make_request.await_count
            await dispatcher.feed_update(bot, callback(19, "menu:progress", user_id=99))
            assert bot.session.make_request.await_count == denied_count
            await dispatcher.feed_update(bot, callback(20, "menu:progress"))
            assert "прочитано 1/3" in edits(bot)[-1].text
            assert "показанных ответов: 1" in edits(bot)[-1].text

    asyncio.run(exercise())


def test_quiz_menu_text_answer_and_html_escaping(tmp_path: Path):
    async def exercise():
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "bot.sqlite3"))
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "menu:quiz"))
            await dispatcher.feed_update(bot, callback(2, button(edits(bot)[-1], "Networking Fundamentals")))
            await dispatcher.feed_update(bot, callback(3, button(edits(bot)[-1], "IP Addressing")))
            await dispatcher.feed_update(bot, callback(4, button(edits(bot)[-1], "Начать новый")))
            await dispatcher.feed_update(bot, callback(5, button(edits(bot)[-1], "24 бита")))
            await dispatcher.feed_update(bot, callback(6, button(edits(bot)[-1], "Следующий вопрос")))
            await dispatcher.feed_update(bot, callback(7, button(edits(bot)[-1], "10.4.5.6")))
            await dispatcher.feed_update(bot, callback(8, button(edits(bot)[-1], "Следующий вопрос")))
            assert "Вопрос 3/5" in edits(bot)[-1].text
            await dispatcher.feed_update(bot, user_text(9, "  255.255.255.0  "))
            assert "1.00/1" in edits(bot)[-1].text
            before = len(edits(bot))
            await dispatcher.feed_update(bot, user_text(10, "255.255.255.0"))
            assert len(edits(bot)) == before

    asyncio.run(exercise())


def test_quiz_results_and_progress_score_without_ai_key(tmp_path: Path):
    async def exercise():
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "bot.sqlite3",
                                                openrouter_api_key="", openai_api_key=""))
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            step = 0

            async def tap(data):
                nonlocal step
                step += 1
                await dispatcher.feed_update(bot, callback(step, data))

            await tap("menu:quiz")
            await tap(button(edits(bot)[-1], "Networking Fundamentals"))
            await tap(button(edits(bot)[-1], "IP Addressing"))
            await tap(button(edits(bot)[-1], "Начать новый"))
            await tap(button(edits(bot)[-1], "24 бита"))
            await tap(button(edits(bot)[-1], "Следующий вопрос"))
            await tap(button(edits(bot)[-1], "10.4.5.6"))
            await tap(button(edits(bot)[-1], "Следующий вопрос"))
            step += 1
            await dispatcher.feed_update(bot, user_text(step, "255.255.255.0"))
            await tap(button(edits(bot)[-1], "Следующий вопрос"))
            await tap(button(edits(bot)[-1], "отправки трафика"))
            await tap(button(edits(bot)[-1], "Следующий вопрос"))
            step += 1
            await dispatcher.feed_update(bot, user_text(step, "10.0.0.0/8, 192.168.0.0/16"))
            await tap(button(edits(bot)[-1], "Результат"))
            assert "Результат" in edits(bot)[-1].text
            assert "5.00/5" in edits(bot)[-1].text
            await tap(button(edits(bot)[-1], "Посмотреть прогресс"))
            assert "последний квиз 5.00/5" in edits(bot)[-1].text
            assert "освоен" in edits(bot)[-1].text
            assert dispatcher["academy_store"].lesson_progress(42, "ip_subnets")["completed_at_utc"] is None

    asyncio.run(exercise())


def test_telegram_edit_failure_uses_new_message(tmp_path: Path):
    async def exercise():
        catalog = load_catalog()
        store = AcademyStore(tmp_path / "a.sqlite3", catalog)
        views = AcademyViews()
        async with Bot(token="123456:TEST") as bot:
            async def respond(_bot, method, **_):
                if isinstance(method, EditMessageText):
                    raise TelegramBadRequest(method, "message to edit not found")
                if isinstance(method, SendMessage):
                    return Message(message_id=22, date=NOW,
                                   chat=Chat(id=42, type="private"), text=method.text)
                return True
            bot.session.make_request = AsyncMock(side_effect=respond)
            await _show(bot, 42, 10, 42, _new("modules"), views, catalog, store)
            assert views.current(42, 42)[0] == 22

    asyncio.run(exercise())


def test_lesson_html_is_escaped_and_fits_telegram(tmp_path: Path):
    original = load_catalog()
    module = original.module_by_id["networking"]
    lesson = module.lessons[0]
    section = {**lesson.sections[0], "title": "<test>",
               "text": "<b> & dangerous-looking content"}
    altered_lesson = replace(lesson, sections=(section, *lesson.sections[1:]))
    altered_module = replace(module, lessons=(altered_lesson, *module.lessons[1:]))
    catalog = Catalog((altered_module, *(entry for entry in original.modules
                                         if entry.id != module.id)))
    store = AcademyStore(tmp_path / "a.sqlite3", catalog)
    text, _ = render(_new("lesson", module_id=module.id, lesson_id=lesson.id, section=1),
                     42, catalog, store)
    assert "&lt;test&gt;" in text
    assert "&lt;b&gt; &amp; dangerous-looking content" in text
    assert len(text) < 3900


def test_adaptive_review_navigation_stale_buttons_and_allowlist(tmp_path: Path):
    async def exercise():
        settings = Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "bot.sqlite3",
                            openrouter_api_key="", openai_api_key="")
        dispatcher = create_dispatcher(settings)
        adaptive = dispatcher["adaptive_store"]
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "menu:learn"))
            await dispatcher.feed_update(bot, callback(2, button(edits(bot)[-1], "Повторить темы")))
            assert "нет вопросов" in edits(bot)[-1].text
            with adaptive._connect() as connection:
                adaptive._record(connection, 42, "ip_q1", "quiz:seed", "quiz", 0, 0, False,
                                 datetime.now(UTC) - timedelta(days=2))
            await dispatcher.feed_update(bot, callback(3, "menu:learn"))
            review_button = button(edits(bot)[-1], "Повторить темы")
            denied_count = bot.session.make_request.await_count
            await dispatcher.feed_update(bot, callback(4, review_button, user_id=99))
            assert bot.session.make_request.await_count == denied_count
            await dispatcher.feed_update(bot, callback(5, review_button))
            assert "Повторение 1/1" in edits(bot)[-1].text
            first_hint = button(edits(bot)[-1], "Подсказка")
            await dispatcher.feed_update(bot, callback(6, first_hint))
            before = len(edits(bot))
            await dispatcher.feed_update(bot, callback(7, first_hint))
            assert len(edits(bot)) == before
            assert "outdated" in bot.session.make_request.await_args.args[1].text
            await dispatcher.feed_update(bot, callback(8, button(edits(bot)[-1], "Подсказка")))
            await dispatcher.feed_update(bot, callback(9, button(edits(bot)[-1], "Подсказка")))
            await dispatcher.feed_update(bot, callback(10, button(edits(bot)[-1], "Показать ответ")))
            assert "0.00/1" in edits(bot)[-1].text
            await dispatcher.feed_update(bot, callback(11, button(edits(bot)[-1], "Результат")))
            assert "Повторение завершено" in edits(bot)[-1].text
            await dispatcher.feed_update(bot, callback(12, button(edits(bot)[-1], "Посмотреть прогресс")))
            assert "Вопросов к повторению: 0" in edits(bot)[-1].text
            await dispatcher.feed_update(bot, callback(13, button(edits(bot)[-1], "Навыки")))
            assert "IPv4 и подсети" in edits(bot)[-1].text
            await dispatcher.feed_update(bot, callback(14, button(edits(bot)[-1], "К прогрессу")))
            await dispatcher.feed_update(bot, callback(15, button(edits(bot)[-1], "Расписание")))
            assert "Asia/Almaty" in edits(bot)[-1].text

    asyncio.run(exercise())


def test_interrupted_review_resumes_after_new_dispatcher(tmp_path: Path):
    async def exercise():
        settings = Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "bot.sqlite3")
        first = create_dispatcher(settings)
        with first["adaptive_store"]._connect() as connection:
            first["adaptive_store"]._record(connection, 42, "ip_q3", "quiz:seed", "quiz",
                                             0, 0, False, datetime.now(UTC) - timedelta(days=2))
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await first.feed_update(bot, callback(1, "menu:learn"))
            await first.feed_update(bot, callback(2, button(edits(bot)[-1], "Повторить темы")))
            assert "Отправьте короткий ответ" in edits(bot)[-1].text
            session_id = first["adaptive_store"].active_review_session(42)["id"]
            restarted = create_dispatcher(settings)
            await restarted.feed_update(bot, callback(3, "menu:learn"))
            await restarted.feed_update(bot, callback(4, button(edits(bot)[-1], "Повторить темы")))
            assert restarted["adaptive_store"].active_review_session(42)["id"] == session_id
            await restarted.feed_update(bot, user_text(5, "255.255.255.0"))
            assert "1.00/1" in edits(bot)[-1].text
            await restarted.feed_update(bot, callback(6, button(edits(bot)[-1], "Результат")))
            assert "Повторение завершено" in edits(bot)[-1].text

    asyncio.run(exercise())


def test_recommended_lesson_button_explains_and_opens_lesson(tmp_path: Path):
    async def exercise():
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "bot.sqlite3"))
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "menu:learn"))
            await dispatcher.feed_update(bot, callback(2, button(edits(bot)[-1], "Следующий урок")))
            assert "IP Addressing and Subnets" in edits(bot)[-1].text
            assert "не отмечен как прочитанный" in edits(bot)[-1].text
            await dispatcher.feed_update(bot, callback(3, button(edits(bot)[-1], "Открыть урок")))
            assert "Цели" in edits(bot)[-1].text
            assert dispatcher["academy_store"].last_studied(42) == ("ip_subnets", 0)

    asyncio.run(exercise())
