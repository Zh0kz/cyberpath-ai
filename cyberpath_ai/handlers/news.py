"""One-article news view with safe navigation and source links."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
import json
import logging
import re
import secrets

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    LinkPreviewOptions, Message,
)

from cyberpath_ai.ai.explainer import AIExplainer, ExplainError, FIELDS, HEADINGS, LEVELS
from cyberpath_ai.news.models import NewsItem
from cyberpath_ai.news.service import NewsResult, NewsService
from cyberpath_ai.handlers.navigation import add_navigation
from cyberpath_ai.handlers.ui import screen


logger = logging.getLogger("cyberpath_ai.news.ui")
PREVIEW_OFF = LinkPreviewOptions(is_disabled=True)
PAGE_CALLBACK = re.compile(r"^news:page:([0-9a-f]{8}):(-?\d+):(-?\d+)$")
REFRESH_CALLBACK = re.compile(r"^news:refresh:([0-9a-f]{8})$")
EXPLAIN_CALLBACK = re.compile(r"^news:explain:([0-9a-f]{8}):(\d+):(beginner|intermediate|advanced)$")
BACK_CALLBACK = re.compile(r"^news:back:([0-9a-f]{8}):(\d+)$")
VIEW_LIFETIME = timedelta(hours=2)


@dataclass
class NewsView:
    token: str
    result: NewsResult
    index: int
    created_at: datetime
    mode: str = "news"
    level: str = "beginner"


class NewsViewStore:
    """Short-lived snapshots bound to a message and the user who opened it."""

    def __init__(self) -> None:
        self._views: dict[tuple[int, int, int], NewsView] = {}
        self._epochs: dict[tuple[int, int], int] = {}

    def epoch(self, chat_id: int, user_id: int) -> int:
        return self._epochs.get((chat_id, user_id), 0)

    def _key(self, message: Message, user_id: int) -> tuple[int, int, int]:
        return message.chat.id, message.message_id, user_id

    def put(self, message: Message, user_id: int, view: NewsView) -> None:
        now = datetime.now(UTC)
        self._views = {
            key: stored for key, stored in self._views.items()
            if now - stored.created_at < VIEW_LIFETIME
        }
        self._views[self._key(message, user_id)] = view

    def get(self, message: Message, user_id: int) -> NewsView | None:
        key = self._key(message, user_id)
        view = self._views.get(key)
        if view is not None and datetime.now(UTC) - view.created_at >= VIEW_LIFETIME:
            self._views.pop(key, None)
            return None
        return view

    def discard(self, message: Message, user_id: int) -> None:
        self._views.pop(self._key(message, user_id), None)

    def discard_user(self, chat_id: int, user_id: int) -> None:
        self._views = {key: view for key, view in self._views.items()
                       if not (key[0] == chat_id and key[2] == user_id)}
        key = (chat_id, user_id)
        self._epochs[key] = self._epochs.get(key, 0) + 1


def refresh_keyboard(token: str | None = None) -> InlineKeyboardMarkup:
    # Kept for empty/failure views and Stage 2 callback compatibility.
    return add_navigation(InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Обновить новости",
            callback_data=f"news:refresh:{token}" if token else "news:refresh",
        )]
    ]), back_data="nav:home", back_to="меню")


def news_keyboard(view: NewsView) -> InlineKeyboardMarkup:
    item = view.result.items[view.index]
    token = view.token
    index = view.index
    return add_navigation(InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обновить новости", callback_data=f"news:refresh:{token}")],
        [
            InlineKeyboardButton(
                text="◀ Назад", callback_data=f"news:page:{token}:{index}:{index - 1}"
            ),
            InlineKeyboardButton(
                text="Далее ▶", callback_data=f"news:page:{token}:{index}:{index + 1}"
            ),
        ],
        [InlineKeyboardButton(text="Открыть источник", url=item.source_url)],
        [InlineKeyboardButton(text="🧠 Объяснить новость",
                              callback_data=f"news:explain:{token}:{index}:beginner")],
    ]), back_data="nav:home", back_to="меню")


def explanation_keyboard(view: NewsView) -> InlineKeyboardMarkup:
    item = view.result.items[view.index]
    token, index = view.token, view.index
    return add_navigation(InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("✓ " if view.level == level else "") + label,
            callback_data=f"news:explain:{token}:{index}:{level}",
        ) for level, label in LEVELS.items()],
        [InlineKeyboardButton(text="◀️ Назад · К новости",
                              callback_data=f"news:back:{token}:{index}")],
        [InlineKeyboardButton(text="Открыть источник", url=item.source_url)],
    ]))


def format_explanation(item: NewsItem, body: dict[str, object], level: str) -> str:
    def escaped_prefix(value: str, limit: int) -> tuple[str, bool]:
        result = ""
        for index, character in enumerate(value):
            encoded = escape(character)
            if len(result) + len(encoded) > limit:
                return result.rstrip() + "…", True
            result += encoded
        return result, False

    title, _ = escaped_prefix(item.title, 180)
    parts = [f"<b>🧠 Объяснение · {escape(LEVELS[level])}</b>",
             f"<b>{title}</b>"]
    shortened = False
    for field, heading in zip(FIELDS, HEADINGS):
        if field == "example" and (level == "beginner" or not body.get("example")):
            continue
        if field == "terms":
            original = "\n".join(
                f"{entry['term']} — {entry['meaning']}" for entry in body["terms"]
            )
        else:
            original = body[field].strip()
            if field == "mechanism" and level == "beginner" and body.get("example"):
                example = str(body["example"]).strip()
                if not example.casefold().startswith("гипотетический пример:"):
                    example = "гипотетический пример: " + example
                original += "\n" + example
        escaped, clipped = escaped_prefix(original, 320)
        shortened |= clipped
        parts.append(f"<b>{heading}</b>\n{escaped}")
    source = f'<a href="{escape(item.source_url, quote=True)}">Оригинальный источник</a>'
    parts.append(source if len(source) < 1000 else "Оригинальный источник — кнопка ниже")
    if shortened:
        parts.append("(Объяснение сокращено для Telegram.)")
    return "\n\n".join(parts)


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _article_text(item: NewsItem, index: int, total: int) -> str:
    date = (
        item.published_at.strftime("%Y-%m-%d")
        if item.date_precision == "day"
        else item.published_at.strftime("%Y-%m-%d %H:%M UTC")
    )
    title = _short(item.title, 180)
    cves = ", ".join(item.cve_ids)
    if cves and cves not in title:
        title += f" ({cves})"
    description = _short(item.description, 350) or "Источник не предоставил описание."
    # Source content stays separate from any future Russian explanation.
    return screen(f"📰 Новости · {index + 1}/{total}",
                  "Краткое описание из публикации; детали — в оригинале.",
                  f"<b>{escape(title)}</b>\n{escape(item.source)} · {date}",
                  f"<b>Описание источника</b>\n{escape(description)}",
                  f'<a href="{escape(item.source_url, quote=True)}">Оригинальный источник</a>')


def format_news(result: NewsResult, index: int = 0) -> str:
    if result.failed:
        text = screen("📰 Новости недоступны", "Источники временно не отвечают. Попробуйте обновить позже.")
    elif not result.items:
        text = screen("📰 Пока нет новых материалов",
                      "За выбранный период не найдено новостей с подтверждённым источником.")
    else:
        safe_index = min(max(index, 0), len(result.items) - 1)
        text = _article_text(result.items[safe_index], safe_index, len(result.items))
    if result.from_cache:
        text += "\n\n(из сохранённых публикаций; cached)"
    if result.failed_sources and result.items:
        names = escape(", ".join(result.failed_sources))
        text += f"\n\nНекоторые источники временно недоступны: {names}."
    if result.general_error and result.items:
        text += "\n\nОбновление не удалось; показаны сохранённые публикации."
    if result.cooldown_seconds:
        text += f"\n\nRefresh cooldown: try again in {result.cooldown_seconds}s."
    return text


def _log_ui_error(action: str, error: Exception) -> None:
    logger.warning(json.dumps({
        "event": "news_telegram_error", "action": action,
        "error_type": type(error).__name__,
    }, sort_keys=True))


async def _answer_callback(query: CallbackQuery, text: str | None = None, *, alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=alert)
    except TelegramAPIError as error:
        _log_ui_error("answer_callback", error)


async def _edit(message: Message, text: str, keyboard: InlineKeyboardMarkup | None = None) -> bool:
    try:
        await message.edit_text(
            text, parse_mode="HTML", link_preview_options=PREVIEW_OFF,
            reply_markup=keyboard,
        )
        return True
    except TelegramBadRequest as error:
        if "message is not modified" in error.message.lower():
            return True
        _log_ui_error("edit_message", error)
    except TelegramAPIError as error:
        _log_ui_error("edit_message", error)
    return False


async def _send(
    message: Message, text: str, keyboard: InlineKeyboardMarkup | None = None
) -> Message | None:
    try:
        sent = await message.answer(
            text, parse_mode="HTML", link_preview_options=PREVIEW_OFF,
            reply_markup=keyboard,
        )
        return sent if isinstance(sent, Message) else None
    except TelegramAPIError as error:
        _log_ui_error("send_message", error)
        return None


async def _load_news(
    query: CallbackQuery, news_service: NewsService, views: NewsViewStore, *, refresh: bool
) -> None:
    await _answer_callback(query, "Loading news…")
    if not isinstance(query.message, Message):
        return
    message = query.message
    opening_epoch = views.epoch(message.chat.id, query.from_user.id)
    views.discard(message, query.from_user.id)
    loading_keyboard = add_navigation(InlineKeyboardMarkup(inline_keyboard=[]))
    if not await _edit(message, "Loading cybersecurity news…", loading_keyboard):
        replacement = await _send(message, "Loading cybersecurity news…", loading_keyboard)
        if replacement is None:
            return
        message = replacement

    try:
        result = await news_service.get_news(refresh=refresh)
    except Exception as error:
        _log_ui_error("load_news", error)
        result = None

    if views.epoch(message.chat.id, query.from_user.id) != opening_epoch:
        return

    if result is None:
        text = "News is unavailable right now. Please try Refresh News later."
        view = NewsView(secrets.token_hex(4), NewsResult(()), 0, datetime.now(UTC))
        keyboard = refresh_keyboard(view.token)
    elif result.items:
        view = NewsView(secrets.token_hex(4), result, 0, datetime.now(UTC))
        text = format_news(result)
        keyboard = news_keyboard(view)
    else:
        view = NewsView(secrets.token_hex(4), result, 0, datetime.now(UTC))
        text = format_news(result)
        keyboard = refresh_keyboard(view.token)

    if await _edit(message, text, keyboard):
        if view is not None:
            views.put(message, query.from_user.id, view)
        return
    replacement = await _send(message, text, keyboard)
    if replacement is not None and view is not None:
        views.put(replacement, query.from_user.id, view)


async def _navigate(query: CallbackQuery, views: NewsViewStore) -> None:
    if not isinstance(query.message, Message):
        await _answer_callback(query, "This news view has expired. Open News again.", alert=True)
        return
    match = PAGE_CALLBACK.fullmatch(query.data or "")
    view = views.get(query.message, query.from_user.id)
    if match is None or view is None or match.group(1) != view.token:
        await _answer_callback(query, "This news view has expired. Open News again.", alert=True)
        return
    if view.mode != "news":
        await _answer_callback(query, "This button is outdated. Use the current buttons.")
        return
    previous, target = int(match.group(2)), int(match.group(3))
    if previous != view.index:
        await _answer_callback(query, "This button is outdated. Use the current news buttons.")
        return
    if target < 0:
        await _answer_callback(query, "Already at the first article.")
        return
    if target >= len(view.result.items):
        await _answer_callback(query, "Already at the last article.")
        return
    await _answer_callback(query)
    candidate = NewsView(view.token, view.result, target, view.created_at)
    text = format_news(candidate.result, target)
    keyboard = news_keyboard(candidate)
    if await _edit(query.message, text, keyboard):
        view.index = target
        return
    views.discard(query.message, query.from_user.id)
    replacement = NewsView(secrets.token_hex(4), view.result, target, datetime.now(UTC))
    sent = await _send(query.message, text, news_keyboard(replacement))
    if sent is not None:
        views.put(sent, query.from_user.id, replacement)


async def _explain(query: CallbackQuery, views: NewsViewStore,
                   explainer: AIExplainer) -> None:
    if not isinstance(query.message, Message):
        await _answer_callback(query, "This news view has expired. Open News again.", alert=True)
        return
    match = EXPLAIN_CALLBACK.fullmatch(query.data or "")
    view = views.get(query.message, query.from_user.id)
    if match is None or view is None or view.token != match.group(1):
        await _answer_callback(query, "This news view has expired. Open News again.", alert=True)
        return
    index, level = int(match.group(2)), match.group(3)
    if index != view.index or view.mode == "loading":
        await _answer_callback(query, "This button is outdated. Use the current buttons.")
        return
    if view.mode == "explanation" and level == view.level:
        await _answer_callback(query, "This explanation is already shown.")
        return
    await _answer_callback(query, "Preparing explanation…")
    view.mode, view.level = "loading", level
    await _edit(query.message, "🧠 Готовлю объяснение новости…",
                explanation_keyboard(view))
    item = view.result.items[index]
    try:
        body = await explainer.explain(item, level)
        text = format_explanation(item, body, level)
    except ExplainError as error:
        text = screen("🧠 Объяснение недоступно", "Новость и ссылка на источник остаются доступны.",
                      escape(str(error)))
        failed = True
    except Exception as error:
        _log_ui_error("explain_news", error)
        text = screen("🧠 Объяснение недоступно", "Попробуйте позже или вернитесь к новости.")
        failed = True
    else:
        failed = False
    if views.get(query.message, query.from_user.id) is not view or view.mode != "loading":
        return
    view.mode = "error" if failed else "explanation"
    if not await _edit(query.message, text, explanation_keyboard(view)):
        views.discard(query.message, query.from_user.id)
        replacement = NewsView(secrets.token_hex(4), view.result, index,
                               datetime.now(UTC), view.mode, level)
        sent = await _send(query.message, text, explanation_keyboard(replacement))
        if sent is not None:
            views.put(sent, query.from_user.id, replacement)


async def _back(query: CallbackQuery, views: NewsViewStore) -> None:
    if not isinstance(query.message, Message):
        await _answer_callback(query, "This news view has expired. Open News again.", alert=True)
        return
    match = BACK_CALLBACK.fullmatch(query.data or "")
    view = views.get(query.message, query.from_user.id)
    if match is None or view is None or view.token != match.group(1):
        await _answer_callback(query, "This news view has expired. Open News again.", alert=True)
        return
    if int(match.group(2)) != view.index or view.mode == "news":
        await _answer_callback(query, "This button is outdated. Use the current buttons.")
        return
    await _answer_callback(query)
    view.mode = "news"
    if not await _edit(query.message, format_news(view.result, view.index), news_keyboard(view)):
        views.discard(query.message, query.from_user.id)
        replacement = NewsView(secrets.token_hex(4), view.result, view.index, datetime.now(UTC))
        sent = await _send(query.message, format_news(view.result, view.index),
                           news_keyboard(replacement))
        if sent is not None:
            views.put(sent, query.from_user.id, replacement)


async def show_news(
    query: CallbackQuery, news_service: NewsService, news_views: NewsViewStore,
    ai_explainer: AIExplainer, mission_views=None,
) -> None:
    data = query.data or ""
    if data == "menu:news" and mission_views is not None and isinstance(query.message, Message):
        mission_views.forget(query.message.chat.id, query.from_user.id)
    if data.startswith("news:explain:"):
        await _explain(query, news_views, ai_explainer)
        return
    if data.startswith("news:back:"):
        await _back(query, news_views)
        return
    if data.startswith("news:page:"):
        await _navigate(query, news_views)
        return
    if data.startswith("news:refresh:"):
        match = REFRESH_CALLBACK.fullmatch(data)
        view = (
            news_views.get(query.message, query.from_user.id)
            if isinstance(query.message, Message) else None
        )
        if match is None or view is None or view.token != match.group(1):
            await _answer_callback(query, "This news view has expired. Open News again.", alert=True)
            return
    elif data not in {"menu:news", "news:refresh"}:
        await _answer_callback(query, "This news button is outdated. Open News again.", alert=True)
        return
    await _load_news(query, news_service, news_views, refresh=data.startswith("news:refresh"))


def create_router() -> Router:
    router = Router(name="news")
    router.callback_query.register(show_news, F.data == "menu:news")
    router.callback_query.register(show_news, F.data.startswith("news:"))
    return router
