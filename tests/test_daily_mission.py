import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from cyberpath_ai.academy.adaptive import AdaptiveStore
from cyberpath_ai.academy.content import load_catalog
from cyberpath_ai.academy.mission import MissionStore
from cyberpath_ai.academy.grading import grade
from cyberpath_ai.academy.storage import AcademyStore
from cyberpath_ai.app import create_dispatcher
from cyberpath_ai.handlers.mission import MissionViews, _new, _show, mission_action
from cyberpath_ai.config import Settings
from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.service import NewsResult
from cyberpath_ai.news.storage import NewsStore
from cyberpath_ai.scheduler import format_digest


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def article(index: int = 1) -> NewsItem:
    return NewsItem("BleepingComputer", f"Verified <headline {index}>",
                    "Source & details", NOW, f"https://example.org/news/{index}")


def stores(path: Path, clock):
    catalog = load_catalog()
    academy = AcademyStore(path, catalog, clock=clock)
    adaptive = AdaptiveStore(path, catalog, clock=clock)
    mission = MissionStore(path, catalog, adaptive, lambda _uid: "Asia/Almaty", clock=clock)
    return catalog, academy, adaptive, mission


def callback(number: int, data: str, user_id: int = 42) -> Update:
    message = Message(message_id=10, date=NOW, chat=Chat(id=user_id, type="private"),
                      from_user=User(id=100, is_bot=True, first_name="Bot"), text="Panel")
    return Update(update_id=number, callback_query=CallbackQuery(
        id=str(number), from_user=User(id=user_id, is_bot=False, first_name="Student"),
        chat_instance="test", data=data, message=message))


def user_text(number: int, text: str, user_id: int = 42) -> Update:
    return Update(update_id=number, message=Message(
        message_id=number, date=NOW, chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Student"), text=text))


def last_edit(bot: Bot) -> EditMessageText:
    return next(call.args[1] for call in reversed(bot.session.make_request.await_args_list)
                if isinstance(call.args[1], EditMessageText))


def button(method: EditMessageText, phrase: str) -> str:
    return next(item.callback_data for row in method.reply_markup.inline_keyboard for item in row
                if phrase in item.text)


def test_stable_mission_midnight_timezone_restart_and_isolation(tmp_path: Path):
    moment = [datetime(2026, 10, 21, 18, 59, tzinfo=UTC)]  # 23:59 in Almaty.
    path = tmp_path / "bot.sqlite"
    catalog, academy, adaptive, store = stores(path, lambda: moment[0])
    first = store.create_today(42, (article(1), article(1), article(2)))
    assert first.local_date.isoformat() == "2026-10-21"
    assert first.news_ids == (article(1).identity_key, article(2).identity_key)
    assert len(first.quiz_ids) == 5 and not first.review_ids
    assert first.available == 3 and first.done == 0
    assert store.create_today(42, (article(3),)).id == first.id
    assert store.get(99) is None
    restarted = MissionStore(path, catalog, adaptive, lambda _uid: "Asia/Almaty",
                             clock=lambda: moment[0])
    assert restarted.get(42) == first
    moment[0] += timedelta(minutes=2)
    next_day = restarted.create_today(42, (article(3),))
    assert next_day.id != first.id and next_day.local_date.isoformat() == "2026-10-22"
    assert restarted.get(42, first.local_date).news_ids == first.news_ids


def test_mission_completion_requires_actual_evidence_and_is_idempotent(tmp_path: Path):
    path = tmp_path / "bot.sqlite"
    catalog, academy, adaptive, store = stores(path, lambda: NOW)
    mission = store.create_today(42, (article(),))
    lesson = catalog.lesson(mission.lesson_id)
    assert store.mark_lesson_summary(42, lesson.id).done == 0
    assert store.mark_news_viewed(42, "other-url").done == 0
    viewed = store.mark_news_viewed(42, article().identity_key)
    assert viewed.done == 1
    assert store.mark_news_viewed(42, article().identity_key).done == 1
    attempt_id = academy.start_attempt(42, lesson)
    assert store.refresh(42).quiz_done is False
    academy.view_section(42, lesson, len(lesson.sections) + 1)
    assert store.mark_lesson_summary(42, lesson.id).lesson_done
    assert store.refresh(42).done == 2
    for index, question in enumerate(lesson.questions):
        run = academy.ensure_run(42, attempt_id, index)
        answer = (question["answer"] if question["kind"] == "choice" else
                  question["accepted"][0] if question["kind"] == "text" else
                  ", ".join(part["aliases"][0] for part in question["parts"]))
        assert academy.submit(42, attempt_id, run["id"], answer, grade(question, answer))
        assert academy.advance(42, attempt_id, run["id"])
    finished = store.refresh(42)
    assert finished.quiz_done and finished.completed_at and finished.done == 3
    assert store.refresh(42).completed_at == finished.completed_at
    restarted = MissionStore(path, catalog, adaptive, lambda _uid: "Asia/Almaty",
                             clock=lambda: NOW)
    assert restarted.get(42).completed_at == finished.completed_at
    assert restarted.get(99) is None


def test_telegram_mission_quiz_completes_only_after_final_advance_across_restart(tmp_path: Path):
    async def exercise():
        settings = Settings("123456:TEST", frozenset({42}),
                            news_db_path=tmp_path / "bot.sqlite", openrouter_api_key="",
                            openai_api_key="")
        dispatcher = create_dispatcher(settings)
        for key in ("academy_store", "adaptive_store", "mission_store"):
            dispatcher[key].clock = lambda: NOW
        mission = dispatcher["mission_store"].create_today(42)
        lesson = dispatcher["catalog"].lesson(mission.lesson_id)
        assert not mission.quiz_done
        step = 0

        async with Bot(token=settings.telegram_bot_token) as bot:
            bot.session.make_request = AsyncMock(return_value=True)

            async def tap(data: str, target=dispatcher, user_id=42):
                nonlocal step
                step += 1
                await target.feed_update(bot, callback(step, data, user_id))

            async def answer(index: int, target=dispatcher):
                nonlocal step
                question = lesson.questions[index]
                if question["kind"] == "choice":
                    view = target["academy_views"].current(42, 42)[1]
                    await tap(f"ac:{view.token}:choose:{question['answer']}", target)
                else:
                    value = (question["accepted"][0] if question["kind"] == "text" else
                             ", ".join(part["aliases"][0] for part in question["parts"]))
                    step += 1
                    await target.feed_update(bot, user_text(step, value))

            await tap("menu:mission")
            await tap(button(last_edit(bot), "Продолжить урок"))
            for _ in range(len(lesson.sections) + 1):
                await tap(button(last_edit(bot), "Следующий раздел"))
            assert dispatcher["mission_store"].get(42).lesson_done
            await tap(button(last_edit(bot), "Назад"))
            await tap(button(last_edit(bot), "Продолжить квиз"))
            await tap(button(last_edit(bot), "Начать новый"))
            attempt_id = dispatcher["academy_store"].active_attempt(42)["id"]

            first_view = dispatcher["academy_views"].current(42, 42)[1]
            first_answer = f"ac:{first_view.token}:choose:{lesson.questions[0]['answer']}"
            await answer(0)
            assert not dispatcher["mission_store"].refresh(42).quiz_done
            count = bot.session.make_request.await_count
            await tap(first_answer)
            assert bot.session.make_request.await_count == count + 1
            assert isinstance(bot.session.make_request.await_args.args[1], AnswerCallbackQuery)
            await tap(button(last_edit(bot), "Повторить вопрос"))
            await answer(0)
            await tap(button(last_edit(bot), "Следующий вопрос"))
            assert not dispatcher["mission_store"].refresh(42).quiz_done

            restarted = create_dispatcher(settings)
            for key in ("academy_store", "adaptive_store", "mission_store"):
                restarted[key].clock = lambda: NOW
            assert restarted["academy_store"].active_attempt(42)["id"] == attempt_id
            await tap("menu:mission", restarted)
            assert "1/2" in last_edit(bot).text
            await tap(button(last_edit(bot), "Продолжить квиз"), restarted)
            assert restarted["academy_views"].current(42, 42)[1].question_index == 1
            for index in range(1, len(lesson.questions)):
                await answer(index, restarted)
                assert not restarted["mission_store"].refresh(42).quiz_done
                await tap(button(last_edit(bot), "Результат" if index == 4 else
                                 "Следующий вопрос"), restarted)
            finished = restarted["mission_store"].get(42)
            assert finished.quiz_done and finished.lesson_done
            assert finished.done == finished.available == 2 and finished.completed_at
            attempt = restarted["academy_store"].attempt(42, attempt_id)
            assert attempt["current_index"] == 5 and attempt["completed_at_utc"]
            assert not restarted["academy_store"].advance(
                42, attempt_id, restarted["academy_store"].current_run(42, attempt_id, 4)["id"])
            assert len(restarted["academy_store"].attempt_runs(42, attempt_id)) == 6
            assert restarted["mission_store"].refresh(42).completed_at == finished.completed_at
            await tap(button(last_edit(bot), "Назад"), restarted)
            assert "2/2" in last_edit(bot).text
            denied = bot.session.make_request.await_count
            await tap("menu:mission", restarted, user_id=99)
            assert bot.session.make_request.await_count == denied

    asyncio.run(exercise())


def test_quiz_finished_after_mission_day_does_not_backfill_previous_day(tmp_path: Path):
    moment = [NOW]
    catalog, academy, _, store = stores(tmp_path / "bot.sqlite", lambda: moment[0])
    mission = store.create_today(42)
    lesson = catalog.lesson(mission.lesson_id)
    academy.view_section(42, lesson, len(lesson.sections) + 1)
    assert store.mark_lesson_summary(42, lesson.id).lesson_done
    attempt_id = academy.start_attempt(42, lesson)
    for index, question in enumerate(lesson.questions):
        if index == 4:
            moment[0] += timedelta(days=1)
        run = academy.ensure_run(42, attempt_id, index)
        answer = (question["answer"] if question["kind"] == "choice" else
                  question["accepted"][0] if question["kind"] == "text" else
                  ", ".join(part["aliases"][0] for part in question["parts"]))
        assert academy.submit(42, attempt_id, run["id"], answer, grade(question, answer))
        assert academy.advance(42, attempt_id, run["id"])
    assert academy.attempt(42, attempt_id)["completed_at_utc"]
    yesterday = store.refresh(42, mission.local_date)
    assert yesterday.lesson_done and not yesterday.quiz_done and not yesterday.completed_at


def test_mission_without_news_or_due_reviews(tmp_path: Path):
    _, _, _, store = stores(tmp_path / "db.sqlite", lambda: NOW)
    mission = store.create_today(42)
    assert mission.available == 2
    assert mission.news_ids == mission.review_ids == ()
    assert mission.done == 0


def test_due_review_snapshot_and_finalized_review_evidence(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    catalog, _, adaptive, store = stores(path, lambda: NOW)
    question_id = catalog.modules[0].lessons[0].questions[0]["id"]
    with sqlite3.connect(path) as db:
        db.execute("""INSERT INTO review_schedules VALUES (?, ?, ?, ?, ?, ?, ?)""",
                   (42, question_id, (NOW - timedelta(days=2)).isoformat(),
                    (NOW - timedelta(days=1)).isoformat(), 1, 0, "incorrect"))
    mission = store.create_today(42)
    assert mission.review_ids == (question_id,)
    assert mission.review_done is False
    session_id = adaptive.start_review_session(42)
    run = adaptive.ensure_review_run(42, session_id, 0)
    question = catalog.question_by_id[question_id][2]
    answer = question["answer"]
    assert adaptive.review_submit(42, session_id, run["id"], answer,
                                  grade(question, answer))
    assert store.refresh(42).review_done is False  # Submission is not yet final.
    assert adaptive.review_advance(42, session_id, run["id"])
    assert store.refresh(42).review_done is True


def test_timezone_change_uses_new_local_date_without_rewriting_old_mission(tmp_path: Path):
    zone = ["Asia/Almaty"]
    moment = datetime(2026, 10, 21, 17, tzinfo=UTC)
    catalog = load_catalog()
    path = tmp_path / "db.sqlite"
    AcademyStore(path, catalog)
    adaptive = AdaptiveStore(path, catalog, clock=lambda: moment)
    store = MissionStore(path, catalog, adaptive, lambda _uid: zone[0], clock=lambda: moment)
    original = store.create_today(42)
    zone[0] = "Europe/London"
    assert store.create_today(42).id == original.id
    zone[0] = "Asia/Tokyo"
    following = store.create_today(42)
    assert following.id != original.id
    assert following.local_date.isoformat() == "2026-10-22"
    assert store.get(42, original.local_date).timezone == "Asia/Almaty"


def test_unfinished_quiz_becomes_mission_when_no_measured_weakness(tmp_path: Path):
    path = tmp_path / "db.sqlite"
    catalog, academy, _, store = stores(path, lambda: NOW)
    lesson = catalog.modules[1].lessons[0]
    attempt_id = academy.start_attempt(42, lesson)
    mission = store.create_today(42)
    assert mission.lesson_id == lesson.id
    assert "незавершённый квиз" in mission.reason
    assert len(mission.quiz_ids) == 5
    assert academy.attempt(42, attempt_id)["completed_at_utc"] is None


def test_mission_handlers_navigation_stale_allowlist_and_no_ai(tmp_path: Path):
    async def exercise():
        news = AsyncMock()
        news.get_news.return_value = NewsResult((article(),))
        settings = Settings("123456:TEST", frozenset({42}), news_db_path=tmp_path / "db.sqlite")
        dispatcher = create_dispatcher(settings, news_service=news)
        dispatcher["mission_store"].clock = lambda: NOW
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "menu:mission"))
            assert "Миссия дня" in last_edit(bot).text
            assert "нет свежих" not in last_edit(bot).text
            assert "<headline" not in last_edit(bot).text
            news_button = button(last_edit(bot), "Verified")
            await dispatcher.feed_update(bot, callback(2, news_button))
            assert "&lt;headline" in last_edit(bot).text
            assert last_edit(bot).link_preview_options.is_disabled
            mission = dispatcher["mission_store"].get(42)
            assert len(mission.viewed_news_ids) == 1
            await dispatcher.feed_update(bot, callback(3, news_button))
            assert isinstance(bot.session.make_request.await_args.args[1], AnswerCallbackQuery)
            assert "outdated" in bot.session.make_request.await_args.args[1].text
            assert len(dispatcher["mission_store"].get(42).viewed_news_ids) == 1
            await dispatcher.feed_update(bot, callback(4, button(last_edit(bot), "К миссии")))
            assert "1/3" in last_edit(bot).text
            await dispatcher.feed_update(bot, callback(5, button(last_edit(bot), "Продолжить урок")))
            assert "Цели" in last_edit(bot).text
            assert dispatcher["mission_store"].get(42).lesson_done is False
            await dispatcher.feed_update(bot, callback(6, button(last_edit(bot), "Назад")))
            assert "Миссия дня" in last_edit(bot).text
            await dispatcher.feed_update(bot, callback(7, button(last_edit(bot), "Продолжить квиз")))
            assert "Пять вопросов" in last_edit(bot).text
            assert dispatcher["mission_store"].get(42).quiz_done is False
            await dispatcher.feed_update(bot, callback(8, "nav:home"))
            assert "CyberPath AI" in last_edit(bot).text
            denied = bot.session.make_request.await_count
            await dispatcher.feed_update(bot, callback(9, "menu:mission", user_id=99))
            assert bot.session.make_request.await_count == denied
            assert dispatcher["mission_store"].get(99) is None
            assert news.get_news.await_count == 1
    asyncio.run(exercise())


def test_digest_opt_in_mission_button_does_not_change_default_format():
    from datetime import date
    text, plain = format_digest((article(),), date(2026, 10, 21),
                                cached=False, older=False)
    assert len(plain.inline_keyboard) == 1
    _, with_mission = format_digest((article(),), date(2026, 10, 21),
                                    cached=False, older=False, include_mission=True)
    assert with_mission.inline_keyboard[-2][0].callback_data == "menu:mission"
    assert with_mission.inline_keyboard[-1][0].callback_data == "nav:home"


def test_mission_uses_recent_cached_news_on_fetch_failure(tmp_path: Path, caplog):
    async def exercise():
        path = tmp_path / "db.sqlite"
        NewsStore(path).save([article()], NOW)
        news = AsyncMock()
        news.get_news.side_effect = OSError("external network detail")
        dispatcher = create_dispatcher(Settings("123456:TEST", frozenset({42}),
                                                news_db_path=path), news_service=news)
        dispatcher["mission_store"].clock = lambda: NOW
        async with Bot(token="123456:TEST") as bot:
            bot.session.make_request = AsyncMock(return_value=True)
            await dispatcher.feed_update(bot, callback(1, "menu:mission"))
            mission = dispatcher["mission_store"].get(42)
            assert mission.news_ids == (article().identity_key,)
            assert button(last_edit(bot), "Verified").startswith("mission:")
    asyncio.run(exercise())
    assert "external network detail" not in caplog.text


def test_mission_edit_failure_falls_back_to_new_message(tmp_path: Path):
    async def exercise():
        path = tmp_path / "db.sqlite"
        catalog, _, _, store = stores(path, lambda: NOW)
        mission = store.create_today(42)
        original = Message(message_id=10, date=NOW, chat=Chat(id=42, type="private"),
                           text="Old")
        replacement = Message(message_id=11, date=NOW, chat=Chat(id=42, type="private"),
                              text="New")
        bot = SimpleNamespace(
            edit_message_text=AsyncMock(side_effect=TelegramBadRequest(
                EditMessageText(chat_id=42, message_id=10, text="Old"), "cannot edit")),
            send_message=AsyncMock(return_value=replacement))
        views = MissionViews()
        view = _new(mission)
        await _show(bot, original, 42, mission, view, views, catalog, NewsStore(path))
        bot.send_message.assert_awaited_once()
        assert views.get(42, 11, 42) == view
        assert views.get(42, 10, 42) is None
    asyncio.run(exercise())


def test_failed_article_display_does_not_mark_news_viewed(tmp_path: Path):
    async def exercise():
        path = tmp_path / "db.sqlite"
        catalog, academy, adaptive, store = stores(path, lambda: NOW)
        NewsStore(path).save([article()], NOW)
        mission = store.create_today(42, (article(),))
        message = Message(message_id=10, date=NOW, chat=Chat(id=42, type="private"),
                          text="Mission")
        view = _new(mission)
        views = MissionViews()
        views.put(42, 10, 42, view)
        error = TelegramBadRequest(EditMessageText(chat_id=42, message_id=10, text="Old"),
                                   "cannot edit")
        bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=error),
                              send_message=AsyncMock(side_effect=error))
        query = SimpleNamespace(message=message, from_user=SimpleNamespace(id=42),
                                data=f"mission:{view.token}:news:0", answer=AsyncMock())
        await mission_action(query, bot, store, views, NewsStore(path), catalog,
                             academy, adaptive, None)
        assert store.get(42).viewed_news_ids == ()
        assert views.get(42, 10, 42) == view
    asyncio.run(exercise())
