"""Stage 4.4C is entirely offline: no website, AI, or Telegram network requests."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, Update, User
import pytest

from cyberpath_ai.academy.adaptive import AdaptiveStore
from cyberpath_ai.academy.content import load_catalog
from cyberpath_ai.academy.resource_recommendations import ResourceRecommender, ResourceStore
from cyberpath_ai.academy.resources import ResourceError, load_resources
from cyberpath_ai.academy.storage import AcademyStore
from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.academy import _new
from cyberpath_ai.handlers.resources import ResourceView, render


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def services(tmp_path: Path):
    catalog = load_catalog()
    path = tmp_path / "db.sqlite3"
    academy = AcademyStore(path, catalog)
    adaptive = AdaptiveStore(path, catalog)
    activity = ResourceStore(path)
    resources = load_resources(catalog)
    return catalog, academy, adaptive, activity, ResourceRecommender(
        catalog, resources, academy, adaptive, activity)


def test_catalog_is_curated_valid_and_all_mappings_exist():
    catalog = load_catalog()
    resources = load_resources(catalog)
    assert len(resources.resources) == 16
    assert {item.category for item in resources.resources} == {"video", "labs", "reading", "practice"}
    assert resources.by_id["thm_network_module"].access == "Unknown"
    assert resources.by_id["htb_starting_point"].access == "Unknown"
    assert all(item.url.startswith("https://") for item in resources.resources)
    assert all(item.lesson_ids and item.skill_ids for item in resources.resources)


@pytest.mark.parametrize("change", [
    lambda data: data["resources"][0].pop("id"),
    lambda data: data["resources"][1].update(id=data["resources"][0]["id"]),
    lambda data: data["resources"][0].update(url="javascript:alert(1)"),
    lambda data: data["resources"][0].update(url="https://["),
    lambda data: data["resources"][0].update(url="https://user:password@example.com/"),
    lambda data: data["resources"][0].update(lesson_ids=["missing_lesson"]),
    lambda data: data["resources"][0].update(skill_ids=["missing_skill"]),
    lambda data: data["resources"][0].update(skill_ids=["safe_dom"]),
    lambda data: data["resources"][0].update(prerequisite_lesson_ids=["missing_lesson"]),
    lambda data: data["resources"][0].update(prerequisites=[""]),
])
def test_catalog_rejects_missing_duplicate_invalid_url_and_mapping(tmp_path, change):
    catalog = load_catalog()
    source = Path("cyberpath_ai/academy/resources.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "resources.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ResourceError):
        load_resources(catalog, path)


def test_recommendations_are_relevant_free_and_unassessed_not_weak(tmp_path):
    catalog, academy, adaptive, activity, engine = services(tmp_path)
    first = engine.recommend(42, "ip_subnets")
    assert first and len(first) <= 3
    assert all("ip_subnets" in item.resource.lesson_ids for item in first)
    assert first[0].resource.access == "Free"
    assert [item.resource.access for item in first] == sorted(
        (item.resource.access for item in first), key=lambda status: status != "Free")
    assert all("оценена" not in item.reason for item in first)
    with adaptive._connect() as connection:
        adaptive._record(connection, 42, "ip_q1", "quiz:weak", "quiz", 0, 0, False,
                         datetime.now(UTC))
    after = engine.recommend(42, "ip_subnets")
    assert any("оценена" in item.reason for item in after)
    assert all("оценена" not in item.reason or "IPv4" in item.reason for item in after)


def test_module_completion_path_and_prerequisites(tmp_path):
    catalog, academy, adaptive, activity, engine = services(tmp_path)
    assert "htb_starting_point" not in {item.resource.id for item in
                                        engine.recommend(42, "linux_users_logs", limit=16)}
    for lesson in catalog.module_by_id["linux"].lessons:
        academy.view_section(42, lesson, len(lesson.sections) + 1)
    assert engine.module_completed(42, "linux")
    selected = engine.recommend(42, "linux_users_logs")
    assert any(item.resource.path for item in selected)
    assert any(item.resource.id in {"bandit_intro", "htb_starting_point"} for item in selected)
    assert not engine.module_completed(99, "linux")


def test_advanced_reading_requires_lesson_and_difficulty_progression(tmp_path):
    catalog, academy, adaptive, activity, engine = services(tmp_path)
    assert engine.recommend(42, "xss", category="reading") == []
    lesson = catalog.lesson("xss")
    academy.view_section(42, lesson, len(lesson.sections) + 1)
    assert engine.recommend(42, "xss", category="reading") == []
    activity.set_difficulty(42, "Intermediate")
    assert [item.resource.id for item in engine.recommend(42, "xss", category="reading")] == ["owasp_xss"]
    activity.set_difficulty(42, "Advanced")
    assert engine.recommend(42, "xss", category="reading")[0].resource.difficulty == "Advanced"


def test_preference_repeat_penalty_completion_and_restart_isolation(tmp_path):
    catalog, academy, adaptive, activity, engine = services(tmp_path)
    activity.set_difficulty(42, "Intermediate")
    initial = engine.recommend(42, "ip_subnets", limit=16)
    assert initial
    chosen = initial[0].resource.id
    for _ in range(4):
        activity.recommended(42, [chosen])
    activity.mark_opened(42, chosen)
    later = engine.recommend(42, "ip_subnets", limit=16)
    assert later[0].resource.id != chosen
    activity.mark_completed(42, chosen)
    assert chosen not in {item.resource.id for item in engine.recommend(42, "ip_subnets", limit=16)}
    activity.set_note(42, chosen, "Повторить позже")
    restarted = ResourceStore(tmp_path / "db.sqlite3")
    assert restarted.difficulty(42) == "Intermediate"
    assert restarted.get(42, chosen)["completed_at_utc"]
    assert restarted.get(42, chosen)["note"] == "Повторить позже"
    assert restarted.get(99, chosen) is None
    assert restarted.difficulty(99) == "Beginner"
    assert academy.lesson_progress(42, "ip_subnets") is None  # External completion adds no mastery.


def test_empty_results_and_html_escaping(tmp_path):
    catalog, academy, adaptive, activity, engine = services(tmp_path)
    assert engine.recommend(42, "missing") == []
    assert engine.recommend(42, "ip_subnets", category="reading") == []
    resource = replace(engine.recommend(42, "ip_subnets")[0].resource,
                       title="<bad> & text", provider="<provider>")
    suggestion = replace(engine.recommend(42, "ip_subnets")[0], resource=resource)
    view = ResourceView("1234abcd", "ip_subnets", _new("lesson", lesson_id="ip_subnets"),
                        "all", (suggestion,), created_at=datetime.now(UTC))
    text, keyboard = render(view, 42, activity)
    assert "&lt;bad&gt; &amp; text" in text and "&lt;provider&gt;" in text
    assert len(text) < 3900
    assert any("Главное меню" in button.text for row in keyboard.inline_keyboard for button in row)


def callback(number: int, data: str, user_id: int = 42) -> Update:
    return Update(update_id=number, callback_query=CallbackQuery(
        id=str(number), from_user=User(id=user_id, is_bot=False, first_name="Student"),
        chat_instance="resources", data=data,
        message=Message(message_id=10, date=NOW, chat=Chat(id=user_id, type="private"),
                        from_user=User(id=100, is_bot=True, first_name="CyberPath"), text="Panel")))


def edits(bot: Bot):
    return [call.args[1] for call in bot.session.make_request.await_args_list
            if isinstance(call.args[1], EditMessageText)]


def button(method: EditMessageText, phrase: str):
    return next(item.callback_data for row in method.reply_markup.inline_keyboard
                for item in row if phrase in item.text)


def test_telegram_resource_navigation_allowlist_and_ai_disabled(tmp_path):
    async def exercise():
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=tmp_path / "db.sqlite3",
                                                openai_api_key="", openrouter_api_key=""))
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            step = 0

            async def tap(data, user_id=42):
                nonlocal step
                step += 1
                await dispatcher.feed_update(bot, callback(step, data, user_id))

            await tap("menu:learn")
            await tap(button(edits(bot)[-1], "Networking Fundamentals"))
            await tap(button(edits(bot)[-1], "IP Addressing"))
            for _ in range(4):
                await tap(button(edits(bot)[-1], "Следующий раздел"))
            assert "Итог урока" in edits(bot)[-1].text
            await tap(button(edits(bot)[-1], "Дополнительные материалы"))
            screen = edits(bot)[-1]
            assert "Дополнительные материалы" in screen.text
            assert screen.link_preview_options.is_disabled
            assert len(dispatcher["resource_views"].get(42, 10, 42).items) <= 3
            stale = button(screen, "Видео")
            await tap(button(screen, "Открыть"))
            detail = edits(bot)[-1]
            assert any(item.url and item.url.startswith("https://")
                       for row in detail.reply_markup.inline_keyboard for item in row)
            resource_id = dispatcher["resource_views"].get(42, 10, 42).selected
            assert dispatcher["resource_store"].get(42, resource_id)["opened_at_utc"]
            before = bot.session.make_request.await_count
            await tap(button(detail, "Я прошёл"), user_id=99)
            assert bot.session.make_request.await_count == before
            await tap(button(detail, "Я прошёл"))
            assert dispatcher["resource_store"].get(42, resource_id)["completed_at_utc"]
            assert dispatcher["academy_store"].lesson_progress(42, "ip_subnets")["mastered_at_utc"] is None
            await tap(stale)
            assert isinstance(bot.session.make_request.await_args.args[1], AnswerCallbackQuery)
            assert "устарела" in bot.session.make_request.await_args.args[1].text
            await tap(button(edits(bot)[-1], "К материалам"))
            await tap(button(edits(bot)[-1], "Материалы"))
            assert "нет подходящих материалов" in edits(bot)[-1].text
            await tap(button(edits(bot)[-1], "К Академии"))
            assert "Итог урока" in edits(bot)[-1].text
            await tap(button(edits(bot)[-1], "Дополнительные материалы"))
            await tap(button(edits(bot)[-1], "Главное меню"))
            assert "CyberPath AI" in edits(bot)[-1].text

    asyncio.run(exercise())
