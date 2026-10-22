"""Compact learning dashboard and global navigation."""

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message

from cyberpath_ai.academy.storage import AcademyStore
from cyberpath_ai.academy.adaptive import AdaptiveStore
from cyberpath_ai.academy.content import Catalog
from cyberpath_ai.academy.mission import MissionStore
from cyberpath_ai.handlers.navigation import HOME_DATA
from cyberpath_ai.handlers.ui import paired_keyboard, safe, screen


MENU_ITEMS = (
    ("📰 Новости", "news"),
    ("📚 Обучение", "learn"),
    ("🧠 Тесты", "quiz"),
    ("🎯 Миссия", "mission"),
    ("🔁 Повторение", "review"),
    ("📊 Прогресс", "progress"),
    ("⚙️ Настройки", "settings"),
    ("❓ Помощь", "help"),
)
MENU_NAMES = {key: label for label, key in MENU_ITEMS}


def main_menu(*, continue_learning: bool = False,
              continue_quiz: bool = False) -> InlineKeyboardMarkup:
    pairs = tuple(tuple((label, f"menu:{key}") for label, key in MENU_ITEMS[index:index + 2])
                  for index in range(0, len(MENU_ITEMS), 2))
    keyboard = paired_keyboard(*pairs)
    rows = list(keyboard.inline_keyboard)
    if continue_learning:
        rows.append([InlineKeyboardButton(text="▶️ Продолжить обучение",
                                          callback_data="dash:continue:learn")])
    if continue_quiz:
        rows.append([InlineKeyboardButton(text="▶️ Продолжить квиз",
                                          callback_data="dash:continue:quiz")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_dashboard(user_id: int, academy_store: AcademyStore,
                     adaptive_store: AdaptiveStore, mission_store: MissionStore,
                     catalog: Catalog) -> tuple[str, InlineKeyboardMarkup]:
    snapshot = academy_store.progress_snapshot(user_id)
    completed = sum(bool(row["completed_at_utc"]) for row in snapshot["lessons"])
    total = sum(len(module.lessons) for module in catalog.modules)
    due = len(adaptive_store.due_questions(user_id))
    mission = mission_store.refresh(user_id)
    mission_state = (f"{mission.done}/{mission.available} выполнено" if mission else
                     "ещё не открыта")
    recommendation = adaptive_store.recommend(user_id)
    next_lesson = (safe(recommendation.lesson.title, 120) if recommendation else
                   "все уроки прочитаны; можно повторить материал")
    last = snapshot["last_studied"]
    active_quiz = academy_store.active_attempt(user_id)
    lines = [f"Уроки: {completed}/{total} прочитано",
             f"Повторение: {due} вопрос(ов) к повторению",
             f"Миссия сегодня: {mission_state}",
             f"Следующий урок: {next_lesson}"]
    if not snapshot["lessons"] and not snapshot["attempts"]:
        lines.append("Начните с короткого урока в Академии.")
    text = screen("🚀 CyberPath AI", "Добро пожаловать. Выберите следующий шаг.",
                  "\n".join(lines))
    unfinished_lesson = catalog.lesson(last[0]) if last else None
    return text, main_menu(continue_learning=bool(
        unfinished_lesson and last[1] < len(unfinished_lesson.sections) + 1),
                           continue_quiz=bool(active_quiz))


async def start(message: Message, academy_store: AcademyStore | None = None,
                adaptive_store: AdaptiveStore | None = None,
                mission_store: MissionStore | None = None,
                catalog: Catalog | None = None) -> None:
    if (getattr(message, "from_user", None) and academy_store and adaptive_store
            and mission_store and catalog):
        text, keyboard = render_dashboard(message.from_user.id, academy_store,
                                          adaptive_store, mission_store, catalog)
    else:
        text, keyboard = screen("🚀 CyberPath AI", "Добро пожаловать. Выберите раздел."), main_menu()
    await message.answer(
        text, parse_mode="HTML", reply_markup=keyboard,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


def _clear_views(chat_id: int, user_id: int, news_views, academy_views,
                 settings_views, mission_views=None, resource_views=None) -> None:
    news_views.discard_user(chat_id, user_id)
    academy_views.forget(chat_id, user_id)
    settings_views.forget(chat_id, user_id)
    if mission_views is not None:
        mission_views.forget(chat_id, user_id)
    if resource_views is not None:
        resource_views.forget(chat_id, user_id)


async def menu_command(message: Message, state: FSMContext, news_views,
                       academy_views, settings_views, mission_views=None,
                       academy_store: AcademyStore | None = None,
                       adaptive_store: AdaptiveStore | None = None,
                       mission_store: MissionStore | None = None,
                       catalog: Catalog | None = None, resource_views=None) -> None:
    await state.clear()
    if message.from_user:
        _clear_views(message.chat.id, message.from_user.id, news_views,
                     academy_views, settings_views, mission_views, resource_views)
    await start(message, academy_store, adaptive_store, mission_store, catalog)


async def home_callback(query: CallbackQuery, state: FSMContext, news_views,
                        academy_views, settings_views, mission_views=None,
                        academy_store: AcademyStore | None = None,
                        adaptive_store: AdaptiveStore | None = None,
                        mission_store: MissionStore | None = None,
                        catalog: Catalog | None = None, resource_views=None) -> None:
    await state.clear()
    if isinstance(query.message, Message):
        _clear_views(query.message.chat.id, query.from_user.id, news_views,
                     academy_views, settings_views, mission_views, resource_views)
    await query.answer()
    if not isinstance(query.message, Message):
        return
    if academy_store and adaptive_store and mission_store and catalog:
        text, keyboard = render_dashboard(query.from_user.id, academy_store,
                                          adaptive_store, mission_store, catalog)
    else:
        text, keyboard = screen("🚀 CyberPath AI", "Добро пожаловать. Выберите раздел."), main_menu()
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard,
                                      link_preview_options=LinkPreviewOptions(is_disabled=True))
    except TelegramBadRequest as error:
        if "message is not modified" in error.message.lower():
            return
        try:
            await query.message.answer(text, parse_mode="HTML", reply_markup=keyboard,
                                       link_preview_options=LinkPreviewOptions(is_disabled=True))
        except TelegramAPIError:
            return
    except TelegramAPIError:
        try:
            await query.message.answer(text, parse_mode="HTML", reply_markup=keyboard,
                                       link_preview_options=LinkPreviewOptions(is_disabled=True))
        except TelegramAPIError:
            return


async def menu_selection(query: CallbackQuery) -> None:
    key = query.data.removeprefix("menu:") if query.data else ""
    name = MENU_NAMES.get(key)
    if name is None:
        await query.answer("Unknown menu item.", show_alert=True)
        return
    await query.answer("Откройте раздел через /menu.", show_alert=True)


def create_router() -> Router:
    router = Router(name="menu")
    router.message.register(menu_command, CommandStart())
    router.message.register(menu_command, Command("menu"))
    router.callback_query.register(home_callback, F.data == HOME_DATA)
    router.callback_query.register(
        menu_selection,
        lambda query: query.data is not None
        and query.data.startswith("menu:")
        and query.data not in {"menu:news", "menu:settings", "menu:learn",
                               "menu:quiz", "menu:progress", "menu:mission",
                               "menu:review", "menu:help"},
    )
    return router
