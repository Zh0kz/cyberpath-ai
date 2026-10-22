"""Offline investigation evidence, persistence, and Telegram regression tests."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cyberpath_ai.academy.content import load_catalog
from cyberpath_ai.academy.investigations import (InvestigationError, grade_step,
                                                  load_investigations)
from cyberpath_ai.academy.investigation_storage import InvestigationStore
from cyberpath_ai.academy.resources import load_resources
from cyberpath_ai.academy.storage import AcademyStore
from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.investigations import InvestigationViews, _new, render


NOW = datetime(2026, 10, 22, tzinfo=UTC)


@pytest.fixture
def catalog_pair():
    lessons = load_catalog()
    resources = load_resources(lessons)
    return lessons, resources, load_investigations(lessons, resources)


def test_catalog_and_technical_evidence(catalog_pair):
    lessons, resources, catalog = catalog_pair
    assert len(catalog.scenarios) == 3
    assert all(len(item.steps) == 5 for item in catalog.scenarios)
    assert all(sum(step.kind != "choice" for step in item.steps) >= 3
               for item in catalog.scenarios)
    assert all(len(set(step.hints)) == 3 for item in catalog.scenarios for step in item.steps)
    assert "drwxrws---" in catalog.by_id["shared_directory"].steps[-1].evidence
    assert "ack=101" in catalog.by_id["dns_tcp_trace"].steps[1].evidence
    assert "ack=501" in catalog.by_id["dns_tcp_trace"].steps[1].evidence
    assert "не подтверждена" in catalog.by_id["soc_login"].steps[-1].answer_display
    for scenario in catalog.scenarios:
        assert scenario.lesson_id in lessons.lesson_by_id
        assert all(resource_id in resources.by_id for resource_id in scenario.resource_ids)
        assert all("198.51.100" not in item.evidence or scenario.id == "dns_tcp_trace"
                   for item in scenario.steps)


@pytest.mark.parametrize("mutation", [
    lambda raw: raw["scenarios"][0]["steps"][0].update(hints=["same"] * 3),
    lambda raw: raw["scenarios"][0]["steps"][0].update(id="bad id"),
    lambda raw: raw["scenarios"][0].update(resource_ids=["unmapped"]),
    lambda raw: raw["scenarios"][1]["steps"][3].update(answer="unknown"),
    lambda raw: raw["scenarios"][0]["steps"][0].update(answer_display="000"),
    lambda raw: raw["scenarios"][2].update(steps=raw["scenarios"][2]["steps"][:3]),
])
def test_schema_rejects_invalid_scenarios(tmp_path: Path, catalog_pair, mutation):
    lessons, resources, _ = catalog_pair
    source = Path("cyberpath_ai/academy/investigations.json")
    raw = deepcopy(json.loads(source.read_text(encoding="utf-8")))
    mutation(raw)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(InvestigationError):
        load_investigations(lessons, resources, path)


def test_equivalent_answers_feedback_and_partial_credit(catalog_pair):
    _, _, catalog = catalog_pair
    linux = catalog.by_id["shared_directory"]
    network = catalog.by_id["dns_tcp_trace"]
    assert grade_step(linux.steps[0], " 777 ").points == 1
    assert grade_step(network.steps[1], "SYN, SYN+ACK, ACK").points == 1
    assert grade_step(linux.steps[2], "write").points == 0.5
    assert grade_step(linux.steps[2], "write, execute").points == 1
    assert grade_step(linux.steps[2], "write, unknown").points == 0
    wrong = grade_step(network.steps[4], "tcp")
    assert wrong.points == 0
    assert "правильный" not in wrong.feedback.lower()
    assert grade_step(catalog.by_id["soc_login"].steps[1], "4, SUCCESS").points == 1


def test_store_hints_reveal_retry_and_completed_history(tmp_path: Path, catalog_pair):
    lessons, _, catalog = catalog_pair
    db = tmp_path / "bot.sqlite3"
    academy = AcademyStore(db, lessons, clock=lambda: NOW)
    academy.view_section(42, lessons.lesson("linux_permissions"), 1)
    store = InvestigationStore(db, catalog, clock=lambda: NOW)
    attempt_id = store.start_or_resume(42, "shared_directory")
    assert store.start_or_resume(42, "shared_directory") == attempt_id
    assert store.summary(42) == {"completed": 0, "active": 1}
    assert store.summary(99) == {"completed": 0, "active": 0}
    run = store.ensure_run(42, attempt_id)
    assert store.ensure_run(42, attempt_id)["id"] == run["id"]
    assert store.reveal(42, attempt_id, run["id"]) is False
    for expected in (1, 2, 3):
        assert store.hint(42, attempt_id, run["id"], expected - 1)["hints_used"] == expected
        assert store.hint(42, attempt_id, run["id"], expected - 1) is None
    assert store.hint(42, attempt_id, run["id"], 3) is None
    assert store.reveal(42, attempt_id, run["id"])
    assert not store.submit(42, attempt_id, run["id"], "777")
    assert not store.advance(99, attempt_id, run["id"])
    replacement = store.retry(42, attempt_id, run["id"])
    assert replacement["run_number"] == 2
    assert not store.advance(42, attempt_id, run["id"])
    assert store.submit(42, attempt_id, replacement["id"], "777")
    assert not store.submit(42, attempt_id, replacement["id"], "777")
    assert store.current_run(42, attempt_id)["points"] == 0
    assert store.current_run(42, attempt_id)["accepted"] == 1
    assert "уже было показано" in store.current_run(42, attempt_id)["feedback"]
    assert store.advance(42, attempt_id, replacement["id"])
    assert not store.advance(42, attempt_id, replacement["id"])
    assert len(store.runs(42, attempt_id)) == 2
    assert store.runs(99, attempt_id) == []
    assert store.current_run(42, attempt_id) is None
    # Restart with a new store instance; use the same persistent attempt.
    resumed = InvestigationStore(db, catalog, clock=lambda: NOW)
    assert resumed.active(42, "shared_directory")["current_index"] == 1
    answers = ["yes", "write", "safe", "2770"]
    for answer in answers:
        current = resumed.ensure_run(42, attempt_id)
        assert resumed.submit(42, attempt_id, current["id"], answer)
        assert resumed.advance(42, attempt_id, current["id"])
    completed = resumed.attempt(42, attempt_id)
    assert completed["completed_at_utc"]
    assert completed["total_points"] == 3.5
    assert resumed.summary(42) == {"completed": 1, "active": 0}
    assert resumed.active(42, "shared_directory") is None
    assert resumed.start_or_resume(42, "shared_directory") != attempt_id
    assert completed["id"] == resumed.latest(42, "shared_directory")["id"] - 1
    assert academy.lesson_progress(42, "linux_permissions")["last_section"] == 1


def test_interrupted_attempt_and_debrief_does_not_award_mastery(tmp_path: Path, catalog_pair):
    lessons, resources, catalog = catalog_pair
    db = tmp_path / "bot.sqlite3"
    store = InvestigationStore(db, catalog)
    attempt_id = store.start(42, "dns_tcp_trace")
    first = store.ensure_run(42, attempt_id)
    assert store.submit(42, attempt_id, first["id"], "wrong")
    assert store.attempt(42, attempt_id)["completed_at_utc"] is None
    assert store.summary(42)["completed"] == 0
    restarted = InvestigationStore(db, catalog)
    assert restarted.active(42, "dns_tcp_trace")["id"] == attempt_id
    assert restarted.current_run(42, attempt_id)["submitted_answer"] == "wrong"
    view = _new("step", scenario_id="dns_tcp_trace", attempt_id=attempt_id,
                run_id=first["id"])
    text, keyboard = render(view, 42, catalog, restarted, lessons, resources)
    assert "полный разбор также будет в итогах" in text
    assert "↻ Повторить этот шаг" in str(keyboard)
    with sqlite3.connect(db) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='skill_evidence'").fetchone()


def callback(update_id: int, data: str, user_id: int = 42, message_id: int = 10) -> Update:
    return Update(update_id=update_id, callback_query=CallbackQuery(
        id=str(update_id), from_user=User(id=user_id, is_bot=False, first_name="Student"),
        chat_instance="investigation", data=data,
        message=Message(message_id=message_id, date=NOW,
                        chat=Chat(id=user_id, type="private"),
                        from_user=User(id=100, is_bot=True, first_name="CyberPath"), text="Menu")))


def user_text(update_id: int, text: str, user_id: int = 42) -> Update:
    return Update(update_id=update_id, message=Message(
        message_id=update_id, date=NOW, chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Student"), text=text))


def edits(bot: Bot) -> list[EditMessageText]:
    return [call.args[1] for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], EditMessageText)]


def button(method: EditMessageText, label: str) -> str:
    return next(item.callback_data for row in method.reply_markup.inline_keyboard
                for item in row if label in item.text)


def test_telegram_flow_resume_stale_buttons_and_allowlist(tmp_path: Path):
    async def exercise():
        settings = Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "bot.sqlite3")
        dispatcher = create_dispatcher(settings)
        async with Bot(token=settings.telegram_bot_token) as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "menu:learn"))
            await dispatcher.feed_update(bot, callback(2, button(edits(bot)[-1], "Практические задания")))
            assert "Практические задания" in edits(bot)[-1].text
            assert button(edits(bot)[-1], "В Академию") == "menu:learn"
            assert edits(bot)[-1].link_preview_options.is_disabled
            stale = button(edits(bot)[-1], "Общая папка команды")
            await dispatcher.feed_update(bot, callback(3, stale))
            start = button(edits(bot)[-1], "Начать расследование")
            await dispatcher.feed_update(bot, callback(4, start))
            assert "Шаг 1/5" in edits(bot)[-1].text
            before = len(edits(bot))
            await dispatcher.feed_update(bot, callback(5, start))
            assert len(edits(bot)) == before
            assert isinstance(bot.session.make_request.await_args.args[1], AnswerCallbackQuery)
            denied = bot.session.make_request.await_count
            await dispatcher.feed_update(bot, callback(6, "menu:learn", user_id=99))
            assert bot.session.make_request.await_count == denied
            await dispatcher.feed_update(bot, user_text(7, "777"))
            assert "Баллы: 1.0/1" in edits(bot)[-1].text
            await dispatcher.feed_update(bot, callback(8, button(edits(bot)[-1], "Продолжить расследование")))
            assert "Шаг 2/5" in edits(bot)[-1].text
            await dispatcher.feed_update(bot, callback(9, "nav:home"))
            assert dispatcher["investigation_store"].active(42, "shared_directory")["current_index"] == 1
            await dispatcher.feed_update(bot, callback(10, "menu:learn"))
            await dispatcher.feed_update(bot, callback(11, button(edits(bot)[-1], "Практические задания")))
            await dispatcher.feed_update(bot, callback(12, button(edits(bot)[-1], "Общая папка команды")))
            await dispatcher.feed_update(bot, callback(13, button(edits(bot)[-1], "Продолжить расследование")))
            assert "Шаг 2/5" in edits(bot)[-1].text
            assert dispatcher["investigation_store"].summary(42)["completed"] == 0

    asyncio.run(exercise())


def test_telegram_complete_investigation_and_progress(tmp_path: Path):
    async def exercise():
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "bot.sqlite3"))
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            sequence = 1

            async def press(label: str):
                nonlocal sequence
                data = button(edits(bot)[-1], label)
                await dispatcher.feed_update(bot, callback(sequence, data))
                sequence += 1

            await dispatcher.feed_update(bot, callback(sequence, "menu:learn"))
            sequence += 1
            await press("Практические задания")
            await press("Общая папка команды")
            await press("Начать расследование")
            await dispatcher.feed_update(bot, user_text(sequence, "777"))
            sequence += 1
            await press("Продолжить расследование")
            await press("Да, права каталога")
            await press("Продолжить расследование")
            await dispatcher.feed_update(bot, user_text(sequence, "write, execute"))
            sequence += 1
            await press("Продолжить расследование")
            await press("chmod 2770")
            await press("Продолжить расследование")
            await dispatcher.feed_update(bot, user_text(sequence, "2770"))
            sequence += 1
            await press("Итоги")
            assert "Итоги" in edits(bot)[-1].text
            assert "5.0/5" in edits(bot)[-1].text
            assert "bandit" in str(edits(bot)[-1].reply_markup).lower()
            assert dispatcher["investigation_store"].summary(42)["completed"] == 1
            assert dispatcher["academy_store"].completed_attempts(42) == []
            await dispatcher.feed_update(bot, callback(sequence, "menu:progress"))
            assert "Практические расследования: завершено 1" in edits(bot)[-1].text

    asyncio.run(exercise())


def test_telegram_restart_requires_fresh_buttons_and_resumes_sqlite(tmp_path: Path):
    async def exercise():
        settings = Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "bot.sqlite3")
        first = create_dispatcher(settings)
        async with Bot(token=settings.telegram_bot_token) as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await first.feed_update(bot, callback(1, "menu:learn"))
            await first.feed_update(bot, callback(2, button(edits(bot)[-1], "Практические задания")))
            await first.feed_update(bot, callback(3, button(edits(bot)[-1], "Общая папка команды")))
            await first.feed_update(bot, callback(4, button(edits(bot)[-1], "Начать расследование")))
            old_button = button(edits(bot)[-1], "Подсказка")
            restarted = create_dispatcher(settings)
            before = len(edits(bot))
            await restarted.feed_update(bot, callback(5, old_button))
            assert len(edits(bot)) == before
            assert isinstance(bot.session.make_request.await_args.args[1], AnswerCallbackQuery)
            assert "устарела" in bot.session.make_request.await_args.args[1].text
            await restarted.feed_update(bot, callback(6, "menu:learn"))
            await restarted.feed_update(bot, callback(7, button(edits(bot)[-1], "Практические задания")))
            await restarted.feed_update(bot, callback(8, button(edits(bot)[-1], "Общая папка команды")))
            await restarted.feed_update(bot, callback(9, button(edits(bot)[-1], "Продолжить расследование")))
            assert "Шаг 1/5" in edits(bot)[-1].text
            assert restarted["investigation_store"].active(42, "shared_directory")["id"] == 1

    asyncio.run(exercise())
