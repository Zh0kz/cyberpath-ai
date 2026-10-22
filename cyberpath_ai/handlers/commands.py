"""Native Telegram commands opening the existing bot features."""

from datetime import UTC, datetime
import logging
import secrets

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardMarkup,
                           LinkPreviewOptions, MenuButtonCommands, Message)

from cyberpath_ai.academy.adaptive import AdaptiveStore
from cyberpath_ai.academy.content import Catalog
from cyberpath_ai.academy.mission import MissionStore
from cyberpath_ai.academy.storage import AcademyStore
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.academy import (AcademyViews, _new as academy_new,
                                           review_view, send_academy)
from cyberpath_ai.handlers.menu import _clear_views, start
from cyberpath_ai.handlers.mission import (MissionViews, _new as mission_new,
                                           get_or_create_today, render as render_mission)
from cyberpath_ai.handlers.navigation import add_navigation
from cyberpath_ai.handlers.news import (NewsView, NewsViewStore, format_news,
                                        news_keyboard, refresh_keyboard)
from cyberpath_ai.handlers.settings import SettingsViewStore, format_settings, settings_keyboard
from cyberpath_ai.news.service import NewsResult, NewsService
from cyberpath_ai.news.storage import NewsStore
from cyberpath_ai.scheduler import PreferenceStore


log = logging.getLogger(__name__)
PREVIEW_OFF = LinkPreviewOptions(is_disabled=True)
COMMANDS = (
    BotCommand(command="start", description="🚀 Запустить CyberPath"),
    BotCommand(command="menu", description="🏠 Главное меню"),
    BotCommand(command="news", description="📰 Новости"),
    BotCommand(command="learn", description="📚 Академия"),
    BotCommand(command="quiz", description="🧠 Тесты"),
    BotCommand(command="mission", description="🎯 Миссия дня"),
    BotCommand(command="review", description="🔁 Повторение"),
    BotCommand(command="progress", description="📊 Мой прогресс"),
    BotCommand(command="settings", description="⚙️ Настройки"),
    BotCommand(command="help", description="❓ Помощь"),
)


async def register_native_menu(bot: Bot) -> None:
    """Both API operations are independent; polling can start if either fails."""
    try:
        await bot.set_my_commands(list(COMMANDS))
    except TelegramAPIError as error:
        log.warning("native_commands_registration_failed error_type=%s", type(error).__name__)
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except TelegramAPIError as error:
        log.warning("native_menu_button_registration_failed error_type=%s", type(error).__name__)


HELP_TEXT = ("<b>❓ Помощь · CyberPath</b>\n\n"
             "<b>Начните с урока:</b> /learn → модуль → урок. Читайте разделы по порядку; "
             "позиция сохраняется.\n\n"
             "<b>Проверьте себя:</b> /quiz открывает пять вопросов. Подсказок до трёх; "
             "Показать ответ помогает учиться, но даёт 0 баллов и не подтверждает знание.\n\n"
             "<b>Повторение:</b> /review показывает вопросы, срок которых наступил. "
             "Они планируются по вашим итоговым ответам.\n\n"
             "<b>Новости:</b> /news показывает реальные материалы и ссылки на источники. "
             "В /settings можно включить ежедневную рассылку.\n\n"
             "<b>Миссия дня:</b> /mission объединяет новости, урок, тест и повторение.\n\n"
             "<b>Практические задания:</b> /learn → «🛡️ Практические задания». "
             "Исследуйте вымышленные улики по шагам; попытки сохраняются отдельно от квизов.\n\n"
             "Вернуться можно кнопкой «🏠 Главное меню» или командой /menu.")


def help_keyboard() -> InlineKeyboardMarkup:
    return add_navigation(InlineKeyboardMarkup(inline_keyboard=[]),
                          back_data="nav:home", back_to="меню")


async def help_callback(query: CallbackQuery, bot: Bot) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    try:
        await bot.edit_message_text(HELP_TEXT, chat_id=query.message.chat.id,
                                    message_id=query.message.message_id, parse_mode="HTML",
                                    reply_markup=help_keyboard(), link_preview_options=PREVIEW_OFF)
    except TelegramAPIError:
        try:
            await bot.send_message(query.message.chat.id, HELP_TEXT, parse_mode="HTML",
                                   reply_markup=help_keyboard(), link_preview_options=PREVIEW_OFF)
        except TelegramAPIError as error:
            log.warning("help_display_failed error_type=%s", type(error).__name__)


async def handle_command(message: Message, bot: Bot, state: FSMContext,
                         news_views: NewsViewStore, academy_views: AcademyViews,
                         settings_views: SettingsViewStore, mission_views: MissionViews,
                         news_service: NewsService, news_store: NewsStore,
                         catalog: Catalog, academy_store: AcademyStore,
                         adaptive_store: AdaptiveStore, mission_store: MissionStore,
                         preferences: PreferenceStore, settings: Settings,
                         resource_views=None, investigation_views=None,
                         investigation_store=None) -> None:
    if message.from_user is None:
        return
    name = (message.text or "").split(maxsplit=1)[0].removeprefix("/").split("@", 1)[0].lower()
    if name not in {item.command for item in COMMANDS}:
        return
    await state.clear()
    user_id, chat_id = message.from_user.id, message.chat.id
    _clear_views(chat_id, user_id, news_views, academy_views, settings_views,
                 mission_views, resource_views, investigation_views)
    if message.chat.type != "private":
        try:
            await message.answer("Откройте CyberPath в личном чате с ботом.")
        except TelegramAPIError:
            pass
        return
    if name in {"start", "menu"}:
        await start(message, academy_store, adaptive_store, mission_store, catalog,
                    investigation_store)
        return
    if name == "help":
        try:
            await bot.send_message(chat_id, HELP_TEXT, parse_mode="HTML",
                                   reply_markup=help_keyboard(),
                                   link_preview_options=PREVIEW_OFF)
        except TelegramAPIError as error:
            log.warning("command_display_failed command=help error_type=%s", type(error).__name__)
        return
    if name == "news":
        try:
            result = await news_service.get_news()
        except Exception as error:
            log.warning("command_news_failure error_type=%s", type(error).__name__)
            result = NewsResult((), general_error=True)
        view = NewsView(secrets.token_hex(4), result, 0, datetime.now(UTC))
        keyboard = news_keyboard(view) if result.items else refresh_keyboard(view.token)
        try:
            sent = await bot.send_message(chat_id, format_news(result), parse_mode="HTML",
                                          reply_markup=keyboard, link_preview_options=PREVIEW_OFF)
            if isinstance(sent, Message):
                news_views.put(sent, user_id, view)
        except TelegramAPIError as error:
            log.warning("command_display_failed command=news error_type=%s", type(error).__name__)
        return
    if name in {"learn", "quiz", "review", "progress"}:
        if name == "review":
            view = review_view(user_id, adaptive_store)
        else:
            view = academy_new("progress" if name == "progress" else "modules",
                               mode="quiz" if name == "quiz" else "learn")
        await send_academy(bot, chat_id, user_id, view, academy_views, catalog,
                           academy_store, adaptive_store, investigation_store)
        return
    if name == "settings":
        try:
            sent = await bot.send_message(chat_id, format_settings(preferences, user_id),
                                          parse_mode="HTML", reply_markup=settings_keyboard(),
                                          link_preview_options=PREVIEW_OFF)
            if isinstance(sent, Message):
                settings_views.put(sent, user_id)
        except TelegramAPIError as error:
            log.warning("command_display_failed command=settings error_type=%s", type(error).__name__)
        return
    if name == "mission":
        mission = await get_or_create_today(user_id, mission_store, news_service,
                                            news_store, settings)
        if mission is None:
            return
        view = mission_new(mission)
        text, keyboard = render_mission(mission, view, catalog, news_store)
        try:
            sent = await bot.send_message(chat_id, text, parse_mode="HTML",
                                          reply_markup=keyboard, link_preview_options=PREVIEW_OFF)
            if isinstance(sent, Message):
                mission_views.put(chat_id, sent.message_id, user_id, view)
        except TelegramAPIError as error:
            log.warning("command_display_failed command=mission error_type=%s", type(error).__name__)


def create_router() -> Router:
    router = Router(name="commands")
    router.message.register(handle_command, Command(*(item.command for item in COMMANDS)))
    router.callback_query.register(help_callback, F.data == "menu:help")
    return router
