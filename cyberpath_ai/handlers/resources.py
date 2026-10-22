"""Token-checked Academy resource recommendations; no external service calls."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import logging
import re
import secrets

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message

from cyberpath_ai.academy.resource_recommendations import ResourceRecommender, ResourceStore, ResourceSuggestion
from cyberpath_ai.handlers.academy import AcademyView, AcademyViews, _show as show_academy
from cyberpath_ai.handlers.navigation import add_navigation
from cyberpath_ai.handlers.ui import safe


log = logging.getLogger(__name__)
CALLBACK = re.compile(r"^rs:([0-9a-f]{8}):([a-z]+)(?::([a-z0-9_]+))?$")
LIFETIME = timedelta(hours=2)
PREVIEW_OFF = LinkPreviewOptions(is_disabled=True)
CATEGORIES = {"all": None, "video": "video", "practice": ("labs", "practice"),
              "reading": "reading"}
LEVELS = {"beginner": "Beginner", "intermediate": "Intermediate", "advanced": "Advanced"}
ACCESS = {"Free": "Бесплатно", "Paid": "Платно", "Unknown": "Доступ уточните на сайте"}
DIFFICULTY = {"Beginner": "Начальный", "Intermediate": "Средний", "Advanced": "Продвинутый"}


@dataclass(frozen=True)
class ResourceView:
    token: str
    lesson_id: str
    parent: AcademyView
    category: str
    items: tuple[ResourceSuggestion, ...]
    selected: str = ""
    created_at: datetime = datetime.min.replace(tzinfo=UTC)


class ResourceViews:
    def __init__(self):
        self._views: dict[tuple[int, int, int], ResourceView] = {}
        self._active: dict[tuple[int, int], tuple[int, int, int]] = {}

    def put(self, chat_id: int, message_id: int, user_id: int, view: ResourceView):
        key = (chat_id, message_id, user_id)
        previous = self._active.get((chat_id, user_id))
        if previous and previous != key:
            self._views.pop(previous, None)
        self._views[key] = view
        self._active[(chat_id, user_id)] = key

    def get(self, chat_id: int, message_id: int, user_id: int) -> ResourceView | None:
        key = (chat_id, message_id, user_id)
        view = self._views.get(key)
        if self._active.get((chat_id, user_id)) != key or view is None:
            return None
        if datetime.now(UTC) - view.created_at > LIFETIME:
            self.forget(chat_id, user_id)
            return None
        return view

    def forget(self, chat_id: int, user_id: int) -> None:
        key = self._active.pop((chat_id, user_id), None)
        if key:
            self._views.pop(key, None)


def _button(view: ResourceView, action: str, label: str, param: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=f"rs:{view.token}:{action}" +
                                (f":{param}" if param else ""))


def render(view: ResourceView, user_id: int, store: ResourceStore) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list[InlineKeyboardButton]] = []
    if view.selected:
        selected = next((item for item in view.items if item.resource.id == view.selected), None)
        if selected is None:
            raise ValueError("Unknown resource selection")
        resource = selected.resource
        activity = store.get(user_id, resource.id)
        lines = [f"<b>📚 {safe(resource.title, 180)}</b>",
                 f"{safe(resource.provider, 120)} · {DIFFICULTY[resource.difficulty]} · {ACCESS[resource.access]}",
                 safe(resource.description_ru, 1000),
                 f"<b>Почему:</b> {safe(selected.reason, 800)}",
                 f"<b>Цель:</b> {safe('; '.join(resource.objectives), 400)}",
                 f"Язык: {safe(resource.language, 60)}" +
                 (f" · ≈{resource.duration_minutes} мин" if resource.duration_minutes else ""),
                 "Завершение отмечается вами и не подтверждается платформой." +
                 (" ✅ Отмечено как пройденное." if activity and activity["completed_at_utc"] else "")]
        rows.append([InlineKeyboardButton(text="🔗 Открыть оригинальный ресурс", url=resource.url)])
        rows.append([_button(view, "done", "✅ Я прошёл", resource.id)])
        rows.append([_button(view, "back", "◀️ Назад · К материалам")])
    else:
        lines = ["<b>📚 Дополнительные материалы</b>",
                 "Подборка по пройденной теме и сохранённым ответам. Внешние сайты открывайте осознанно."]
        if not view.items:
            lines.append("По этой теме и категории пока нет подходящих материалов. Выберите другую категорию или уровень.")
        for index, suggestion in enumerate(view.items, 1):
            resource = suggestion.resource
            lines.append(f"<b>{index}. {safe(resource.title, 170)}</b>\n"
                         f"{safe(resource.provider, 90)} · {DIFFICULTY[resource.difficulty]} · {ACCESS[resource.access]}"
                         + (f" · ≈{resource.duration_minutes} мин" if resource.duration_minutes else "")
                         + f"\n{safe(suggestion.reason, 500)}")
            rows.append([_button(view, "open", f"🔗 Открыть · {resource.title[:28]}", resource.id)])
        rows.append([_button(view, "category", "📹 Видео", "video"),
                     _button(view, "category", "💻 Практика", "practice")])
        rows.append([_button(view, "category", "📖 Материалы", "reading"),
                     _button(view, "category", "Все", "all")])
        current = store.difficulty(user_id)
        rows.append([_button(view, "level", ("✓ " if current == level else "") +
                             DIFFICULTY[level], level.lower()) for level in LEVELS.values()])
        rows.append([_button(view, "back", "◀️ Назад · К Академии")])
    return "\n\n".join(lines), add_navigation(InlineKeyboardMarkup(inline_keyboard=rows))


async def _show(bot: Bot, chat_id: int, message_id: int, user_id: int,
                view: ResourceView, views: ResourceViews, store: ResourceStore) -> None:
    text, keyboard = render(view, user_id, store)
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                    parse_mode="HTML", reply_markup=keyboard,
                                    link_preview_options=PREVIEW_OFF)
        views.put(chat_id, message_id, user_id, view)
    except TelegramAPIError:
        try:
            sent = await bot.send_message(chat_id, text, parse_mode="HTML",
                                          reply_markup=keyboard, link_preview_options=PREVIEW_OFF)
            if isinstance(sent, Message):
                views.put(chat_id, sent.message_id, user_id, view)
        except TelegramAPIError as error:
            log.warning("resource_display_failed error_type=%s", type(error).__name__)


async def open_from_academy(query: CallbackQuery, bot: Bot, parent: AcademyView,
                            recommender: ResourceRecommender, store: ResourceStore,
                            resource_views: ResourceViews, academy_views: AcademyViews) -> None:
    user_id = query.from_user.id
    items = tuple(recommender.recommend(user_id, parent.lesson_id))
    store.recommended(user_id, [item.resource.id for item in items])
    view = ResourceView(secrets.token_hex(4), parent.lesson_id, parent, "all", items,
                        created_at=datetime.now(UTC))
    academy_views.forget(query.message.chat.id, user_id)
    await query.answer()
    await _show(bot, query.message.chat.id, query.message.message_id, user_id,
                view, resource_views, store)


async def resource_action(query: CallbackQuery, bot: Bot, resource_views: ResourceViews,
                          resource_store: ResourceStore, resource_recommender: ResourceRecommender,
                          academy_views: AcademyViews, catalog, academy_store, adaptive_store) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != "private":
        await query.answer("Откройте материалы в личном чате.", show_alert=True)
        return
    match = CALLBACK.fullmatch(query.data or "")
    view = resource_views.get(query.message.chat.id, query.message.message_id, query.from_user.id)
    if not match or view is None or match.group(1) != view.token:
        await query.answer("Кнопка устарела. Откройте материалы снова из Академии.", show_alert=True)
        return
    action, param = match.group(2), match.group(3)
    user_id = query.from_user.id
    if action == "back" and view.selected:
        target = replace(view, token=secrets.token_hex(4), selected="", created_at=datetime.now(UTC))
    elif action == "back":
        resource_views.forget(query.message.chat.id, user_id)
        await query.answer()
        parent = replace(view.parent, token=secrets.token_hex(4), created_at=datetime.now(UTC))
        await show_academy(bot, query.message.chat.id, query.message.message_id, user_id,
                           parent, academy_views, catalog, academy_store, adaptive_store)
        return
    elif action == "open" and any(item.resource.id == param for item in view.items):
        # Telegram URL buttons have no callback. Record the user's request for the link,
        # never claim to verify an external visit.
        resource_store.mark_opened(user_id, param)
        target = replace(view, token=secrets.token_hex(4), selected=param,
                         created_at=datetime.now(UTC))
    elif action == "done" and view.selected == param:
        resource_store.mark_completed(user_id, param)
        await query.answer("Отмечено вами как пройденное. Это не проверка платформы.")
        target = replace(view, token=secrets.token_hex(4), created_at=datetime.now(UTC))
        await _show(bot, query.message.chat.id, query.message.message_id, user_id,
                    target, resource_views, resource_store)
        return
    elif action == "category" and param in CATEGORIES:
        items = tuple(resource_recommender.recommend(user_id, view.lesson_id,
                                                    category=CATEGORIES[param]))
        resource_store.recommended(user_id, [item.resource.id for item in items])
        target = replace(view, token=secrets.token_hex(4), selected="", category=param,
                         items=items, created_at=datetime.now(UTC))
    elif action == "level" and param in LEVELS:
        resource_store.set_difficulty(user_id, LEVELS[param])
        items = tuple(resource_recommender.recommend(user_id, view.lesson_id,
                                                    category=CATEGORIES[view.category]))
        resource_store.recommended(user_id, [item.resource.id for item in items])
        target = replace(view, token=secrets.token_hex(4), selected="", items=items,
                         created_at=datetime.now(UTC))
    else:
        await query.answer("Это действие недоступно.", show_alert=True)
        return
    await query.answer()
    await _show(bot, query.message.chat.id, query.message.message_id, user_id,
                target, resource_views, resource_store)


def create_router() -> Router:
    router = Router(name="resources")
    router.callback_query.register(resource_action, F.data.startswith("rs:"))
    return router
