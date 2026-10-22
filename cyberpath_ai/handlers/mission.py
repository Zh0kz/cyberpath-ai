"""Telegram daily mission navigation; all questions remain in Academy."""

from dataclasses import dataclass
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
import re
import secrets

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message

from cyberpath_ai.academy.content import Catalog
from cyberpath_ai.academy.adaptive import AdaptiveStore
from cyberpath_ai.academy.mission import Mission, MissionStore
from cyberpath_ai.academy.storage import AcademyStore
from cyberpath_ai.handlers.academy import AcademyViews, _new as academy_new, _show as academy_show
from cyberpath_ai.handlers.navigation import add_navigation
from cyberpath_ai.handlers.ui import indicator, safe, screen
from cyberpath_ai.news.service import NewsService
from cyberpath_ai.news.storage import NewsStore
from cyberpath_ai.handlers.news import NewsViewStore
from cyberpath_ai.handlers.settings import SettingsViewStore
from cyberpath_ai.config import Settings


CALLBACK = re.compile(r"^mission:([0-9a-f]{8}):(overview|news|lesson|quiz|review)(?::(\d+))?$")
PREVIEW_OFF = LinkPreviewOptions(is_disabled=True)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MissionView:
    token: str
    mission_id: int
    screen: str
    index: int
    created_at: datetime


class MissionViews:
    def __init__(self) -> None:
        self._active: dict[tuple[int, int], tuple[int, MissionView]] = {}
        self._epochs: dict[tuple[int, int], int] = {}

    def epoch(self, chat_id: int, user_id: int) -> int:
        return self._epochs.get((chat_id, user_id), 0)

    def put(self, chat_id: int, message_id: int, user_id: int, view: MissionView) -> None:
        self._active[(chat_id, user_id)] = message_id, view

    def get(self, chat_id: int, message_id: int, user_id: int) -> MissionView | None:
        active = self._active.get((chat_id, user_id))
        if active is None or active[0] != message_id:
            return None
        view = active[1]
        return view if datetime.now(UTC) - view.created_at < timedelta(hours=2) else None

    def forget(self, chat_id: int, user_id: int) -> None:
        self._active.pop((chat_id, user_id), None)
        key = (chat_id, user_id)
        self._epochs[key] = self.epoch(*key) + 1


def _new(mission: Mission, screen: str = "overview", index: int = 0) -> MissionView:
    return MissionView(secrets.token_hex(4), mission.id, screen, index, datetime.now(UTC))


def _button(view: MissionView, action: str, label: str, index: int | None = None,
            url: str | None = None) -> InlineKeyboardButton:
    if url:
        return InlineKeyboardButton(text=label, url=url)
    suffix = f":{index}" if index is not None else ""
    return InlineKeyboardButton(text=label,
                                callback_data=f"mission:{view.token}:{action}{suffix}")


def render(mission: Mission, view: MissionView, catalog: Catalog,
           news_store: NewsStore) -> tuple[str, InlineKeyboardMarkup]:
    lesson = catalog.lesson(mission.lesson_id)
    if view.screen == "news" and 0 <= view.index < len(mission.news_ids):
        article = news_store.get(mission.news_ids[view.index])
        if article:
            date = article.published_at.strftime("%Y-%m-%d" if article.date_precision == "day"
                                                 else "%Y-%m-%d %H:%M UTC")
            text = screen("📰 Материал миссии",
                          f"{article.source} · {date}",
                          f"<b>{safe(article.title, 250)}</b>",
                          safe(article.description or "Нет описания от источника.", 1000),
                          "Открытие материала в боте не подтверждает понимание темы.")
            rows = [[_button(view, "news", "◀ Предыдущая", view.index - 1)]
                    if view.index else []]
            if view.index + 1 < len(mission.news_ids):
                rows.append([_button(view, "news", "Следующая ▶", view.index + 1)])
            rows.append([_button(view, "overview", "◀️ Назад · К миссии")])
            rows.append([_button(view, "overview", "Открыть оригинал", url=article.source_url)])
            return text, add_navigation(InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row]))
    news_state = ("нет свежих подтверждённых материалов" if not mission.news_ids else
                  f"просмотрено {len(mission.viewed_news_ids)}/{len(mission.news_ids)}")
    review_state = ("не требуется сегодня" if not mission.review_ids else
                    "завершено" if mission.review_done else
                    f"ожидает · {len(mission.review_ids)} вопрос(ов)")
    lines = [f"Прогресс: {mission.done}/{mission.available}",
             indicator(bool(mission.news_ids) and
                       len(mission.viewed_news_ids) == len(mission.news_ids),
                       f"Новости: {news_state}"),
             indicator(mission.lesson_done,
                       f"Урок: {lesson.title if lesson else mission.lesson_id}"),
             indicator(mission.quiz_done, "Квиз: 5 вопросов"),
             indicator(mission.review_done or not mission.review_ids,
                       f"Повторение: {review_state}")]
    if mission.completed_at:
        lines.append("Все доступные задачи миссии выполнены.")
    rows = []
    for index, identity in enumerate(mission.news_ids):
        item = news_store.get(identity)
        if item:
            marker = "✓ " if identity in mission.viewed_news_ids else ""
            rows.append([_button(view, "news", marker + item.title[:50], index)])
    rows.append([_button(view, "lesson", "▶ Продолжить урок" if not mission.lesson_done
                         else "Открыть урок")])
    rows.append([_button(view, "quiz", "▶ Продолжить квиз" if not mission.quiz_done
                         else "Повторить квиз")])
    if mission.review_ids:
        rows.append([_button(view, "review", "🔁 Повторить вопросы")])
    text = screen("🎯 Миссия дня", f"{mission.local_date.isoformat()} · {mission.timezone}",
                  "\n".join(lines),
                  "<b>Почему этот урок?</b>\n" + safe(mission.reason, 500))
    return text, add_navigation(
        InlineKeyboardMarkup(inline_keyboard=rows), back_data="nav:home", back_to="меню")


async def _show(bot: Bot, message: Message, user_id: int, mission: Mission,
                view: MissionView, views: MissionViews, catalog: Catalog,
                news_store: NewsStore) -> bool:
    text, keyboard = render(mission, view, catalog, news_store)
    try:
        await bot.edit_message_text(text, chat_id=message.chat.id,
                                    message_id=message.message_id, parse_mode="HTML",
                                    reply_markup=keyboard, link_preview_options=PREVIEW_OFF)
        views.put(message.chat.id, message.message_id, user_id, view)
        return True
    except TelegramBadRequest as error:
        if "message is not modified" in error.message.lower():
            views.put(message.chat.id, message.message_id, user_id, view)
            return True
        try:
            sent = await bot.send_message(message.chat.id, text, parse_mode="HTML",
                                          reply_markup=keyboard, link_preview_options=PREVIEW_OFF)
            if isinstance(sent, Message):
                views.put(message.chat.id, sent.message_id, user_id, view)
                return True
        except TelegramAPIError as send_error:
            log.warning("mission_display_failed error_type=%s", type(send_error).__name__)
    except TelegramAPIError as error:
        log.warning("mission_display_failed error_type=%s", type(error).__name__)
    return False


async def get_or_create_today(user_id: int, mission_store: MissionStore,
                              news_service: NewsService, news_store: NewsStore,
                              settings: Settings,
                              still_current: Callable[[], bool] = lambda: True) -> Mission | None:
    mission = mission_store.get(user_id)
    if mission is None:
        try:
            result = await news_service.get_news()
            articles = tuple(item for item in result.items
                             if item.is_recent(mission_store.now(), timedelta(
                                 hours=settings.news_lookback_hours)))[:3]
            news_store.save(list(articles), mission_store.now())
        except Exception as error:
            log.warning("mission_news_failure error_type=%s", type(error).__name__)
            articles = tuple(news_store.recent(mission_store.now(), timedelta(
                hours=settings.news_lookback_hours))[:3])
        if not still_current():
            return None
        mission = mission_store.create_today(user_id, articles)
    return mission_store.refresh(user_id) or mission


async def open_mission(query: CallbackQuery, bot: Bot, mission_store: MissionStore,
                       mission_views: MissionViews, news_service: NewsService,
                       news_store: NewsStore, catalog: Catalog,
                       academy_views: AcademyViews, settings: Settings,
                       news_views: NewsViewStore | None = None,
                       settings_views: SettingsViewStore | None = None) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != "private":
        await query.answer("Open the mission in a private chat.", show_alert=True)
        return
    await query.answer()
    user_id, chat_id = query.from_user.id, query.message.chat.id
    if news_views:
        news_views.discard_user(chat_id, user_id)
    if settings_views:
        settings_views.forget(chat_id, user_id)
    epoch = mission_views.epoch(chat_id, user_id)
    if mission_store.get(user_id) is None:
        try:
            await bot.edit_message_text("Подбираем миссию из сохранённых уроков и реальных новостей…",
                                        chat_id=chat_id, message_id=query.message.message_id,
                                        reply_markup=add_navigation(
                                            InlineKeyboardMarkup(inline_keyboard=[])))
        except TelegramAPIError:
            pass
    mission = await get_or_create_today(user_id, mission_store, news_service,
                                        news_store, settings,
                                        lambda: mission_views.epoch(chat_id, user_id) == epoch)
    if mission is None or mission_views.epoch(chat_id, user_id) != epoch:
        return
    academy_views.forget(chat_id, user_id)
    await _show(bot, query.message, user_id, mission, _new(mission), mission_views,
                catalog, news_store)


async def mission_action(query: CallbackQuery, bot: Bot, mission_store: MissionStore,
                         mission_views: MissionViews, news_store: NewsStore,
                         catalog: Catalog, academy_store: AcademyStore,
                         adaptive_store: AdaptiveStore, academy_views: AcademyViews) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != "private":
        await query.answer("Open the mission in a private chat.", show_alert=True)
        return
    match = CALLBACK.fullmatch(query.data or "")
    user_id, chat_id = query.from_user.id, query.message.chat.id
    view = mission_views.get(chat_id, query.message.message_id, user_id)
    mission = mission_store.get(user_id)
    if (not match or view is None or mission is None or view.mission_id != mission.id
            or match.group(1) != view.token):
        await query.answer("This mission button is outdated. Open today's mission again.",
                           show_alert=True)
        return
    action = match.group(2)
    index = int(match.group(3)) if match.group(3) else 0
    if action == "news" and not 0 <= index < len(mission.news_ids):
        await query.answer("This article is unavailable.", show_alert=True)
        return
    await query.answer()
    if action == "news":
        shown = await _show(bot, query.message, user_id, mission,
                            _new(mission, "news", index), mission_views, catalog, news_store)
        if shown:
            mission_store.mark_news_viewed(user_id, mission.news_ids[index])
    elif action == "overview":
        mission = mission_store.refresh(user_id) or mission
        await _show(bot, query.message, user_id, mission, _new(mission), mission_views,
                    catalog, news_store)
    else:
        lesson = catalog.lesson(mission.lesson_id)
        if lesson is None:
            return
        if action == "lesson":
            progress = academy_store.lesson_progress(user_id, lesson.id)
            section = min(progress["last_section"], len(lesson.sections) + 1) if progress else 0
            academy_store.view_section(user_id, lesson, section)
            target = academy_new("lesson", module_id=lesson.module_id, lesson_id=lesson.id,
                                 section=section, from_mission=True)
        elif action == "quiz":
            attempt = academy_store.active_attempt(user_id)
            if attempt is not None and attempt["lesson_id"] == lesson.id:
                position = attempt["current_index"]
                run = academy_store.ensure_run(user_id, attempt["id"], position)
                target = academy_new("question", mode="quiz", module_id=lesson.module_id,
                                     lesson_id=lesson.id, attempt_id=attempt["id"],
                                     question_index=position, run_id=run["id"], from_mission=True)
            else:
                target = academy_new("quiz_intro", mode="quiz", module_id=lesson.module_id,
                                     lesson_id=lesson.id, from_mission=True)
        elif action == "review":
            session_id = adaptive_store.start_review_session(user_id)
            if session_id is None:
                target = academy_new("review_empty", from_mission=True)
            else:
                session = adaptive_store.review_session(user_id, session_id)
                position = session["current_index"]
                items = adaptive_store.review_items(user_id, session_id)
                run = adaptive_store.ensure_review_run(user_id, session_id, position)
                target = academy_new("review_question", mode="review", attempt_id=session_id,
                                     question_index=position, lesson_id=items[position]["lesson_id"],
                                     run_id=run["id"], from_mission=True)
        else:
            return
        mission_views.forget(chat_id, user_id)
        await academy_show(bot, chat_id, query.message.message_id, user_id, target,
                           academy_views, catalog, academy_store, adaptive_store)


def create_router() -> Router:
    router = Router(name="mission")
    router.callback_query.register(open_mission, F.data == "menu:mission")
    router.callback_query.register(mission_action, F.data.startswith("mission:"))
    return router
