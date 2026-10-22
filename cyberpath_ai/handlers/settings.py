"""Private, allowlisted daily-news preference controls."""

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from cyberpath_ai.handlers.menu import menu_command
from cyberpath_ai.handlers.navigation import add_navigation, back_button, home_button
from cyberpath_ai.handlers.ui import safe, screen
from cyberpath_ai.scheduler import DailyScheduler, DigestService, PreferenceStore, parse_hour


class TimeForm(StatesGroup):
    hour = State()


class SettingsViewStore:
    def __init__(self) -> None:
        self._active: dict[tuple[int, int], int] = {}

    def put(self, message: Message, user_id: int) -> None:
        self._active[(message.chat.id, user_id)] = message.message_id

    def current(self, message: Message, user_id: int) -> bool:
        return self._active.get((message.chat.id, user_id)) == message.message_id

    def forget(self, chat_id: int, user_id: int) -> None:
        self._active.pop((chat_id, user_id), None)


def settings_keyboard() -> InlineKeyboardMarkup:
    return add_navigation(InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Включить новости", callback_data="settings:enable"),
         InlineKeyboardButton(text="Выключить", callback_data="settings:disable")],
        [InlineKeyboardButton(text="Изменить время (HH)", callback_data="settings:time")],
        [InlineKeyboardButton(text="3 статьи", callback_data="settings:count:3"),
         InlineKeyboardButton(text="5 статей", callback_data="settings:count:5")],
        [InlineKeyboardButton(text="Отправить тестовый выпуск", callback_data="settings:test")],
        [InlineKeyboardButton(text="Отменить ввод времени", callback_data="settings:cancel")],
    ]), back_data="nav:home", back_to="меню")


def time_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [back_button("settings:cancel", "к настройкам")], [home_button()],
    ])


def format_settings(store: PreferenceStore, user_id: int) -> str:
    preference = store.get(user_id)
    if preference is None:
        return "Open Settings in a private chat with the bot."
    status = "включены" if preference.enabled else "выключены — требуется ваше согласие"
    return screen("⚙️ Настройки", "Ежедневные новости безопасности",
                  f"Статус: {status}\n"
                  f"Время доставки: {preference.hour:02d}:00\n"
                  f"Часовой пояс: {safe(preference.timezone, 100)}\n"
                  f"Статей: {preference.article_count}",
                  "Для смены времени введите два числа: 00–23.")


async def _show(query: CallbackQuery, store: PreferenceStore,
                settings_views: SettingsViewStore) -> None:
    if not isinstance(query.message, Message):
        return
    text = format_settings(store, query.from_user.id)
    try:
        await query.message.edit_text(text, parse_mode="HTML",
                                      reply_markup=settings_keyboard())
        settings_views.put(query.message, query.from_user.id)
    except TelegramBadRequest as error:
        if "message is not modified" in error.message.lower():
            settings_views.put(query.message, query.from_user.id)
            return
        try:
            sent = await query.message.answer(text, parse_mode="HTML",
                                              reply_markup=settings_keyboard())
        except TelegramAPIError:
            return
        if isinstance(sent, Message):
            settings_views.put(sent, query.from_user.id)
    except TelegramAPIError:
        try:
            sent = await query.message.answer(text, parse_mode="HTML",
                                              reply_markup=settings_keyboard())
        except TelegramAPIError:
            return
        if isinstance(sent, Message):
            settings_views.put(sent, query.from_user.id)


async def open_settings(query: CallbackQuery, preferences: PreferenceStore,
                        settings_views: SettingsViewStore, mission_views=None) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != "private":
        await query.answer("Open Settings in a private chat with the bot.", show_alert=True)
        return
    if mission_views is not None:
        mission_views.forget(query.message.chat.id, query.from_user.id)
    await query.answer()
    await _show(query, preferences, settings_views)


async def settings_action(query: CallbackQuery, preferences: PreferenceStore,
                          daily_scheduler: DailyScheduler, digest_service: DigestService,
                          state: FSMContext, bot: Bot,
                          settings_views: SettingsViewStore) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != "private":
        await query.answer("Open Settings in a private chat with the bot.", show_alert=True)
        return
    if not settings_views.current(query.message, query.from_user.id):
        await query.answer("This Settings button is outdated. Open Settings again.", show_alert=True)
        return
    action = (query.data or "").removeprefix("settings:")
    user_id = query.from_user.id
    if action == "enable":
        if not preferences.set_enabled(user_id, True):
            await query.answer("Open /start in a private chat first.", show_alert=True)
            return
        daily_scheduler.wake()
    elif action == "disable":
        preferences.set_enabled(user_id, False)
        daily_scheduler.wake()
    elif action == "time":
        await state.set_state(TimeForm.hour)
        await query.answer()
        sent = await query.message.answer("Введите час доставки двумя цифрами (HH, 00–23). "
                                          "Команды /cancel или /menu отменяют ввод.",
                                          reply_markup=time_prompt_keyboard())
        if isinstance(sent, Message):
            settings_views.put(sent, user_id)
        return
    elif action in {"count:3", "count:5"}:
        preferences.set_article_count(user_id, int(action[-1]))
        daily_scheduler.wake()
    elif action == "test":
        await query.answer("Preparing a test digest…")
        outcome = await digest_service.deliver(bot, user_id, scheduled=False)
        if outcome != "sent":
            await query.message.answer("Test digest could not be delivered. Check the bot log and try again.")
        return
    elif action == "cancel":
        await state.clear()
    else:
        await query.answer("Unknown setting.", show_alert=True)
        return
    await query.answer()
    await _show(query, preferences, settings_views)


async def cancel_time(message: Message, state: FSMContext,
                      preferences: PreferenceStore,
                      settings_views: SettingsViewStore) -> None:
    await state.clear()
    sent = await message.answer("Ввод времени отменён.\n\n" + format_settings(preferences, message.from_user.id),
                                parse_mode="HTML", reply_markup=settings_keyboard())
    if isinstance(sent, Message):
        settings_views.put(sent, message.from_user.id)


async def receive_hour(message: Message, state: FSMContext,
                       preferences: PreferenceStore, daily_scheduler: DailyScheduler,
                       settings_views: SettingsViewStore, news_views,
                       academy_views, mission_views=None) -> None:
    if message.text in {"/start", "/menu"}:
        await menu_command(message, state, news_views, academy_views, settings_views,
                           mission_views)
        return
    hour = parse_hour((message.text or "").strip())
    if hour is None:
        await message.answer("Invalid hour. Введите два числа от 00 до 23 или /cancel.")
        return
    preferences.set_hour(message.from_user.id, hour)
    daily_scheduler.wake()
    await state.clear()
    sent = await message.answer("Время доставки обновлено.\n\n" + format_settings(preferences, message.from_user.id),
                                parse_mode="HTML", reply_markup=settings_keyboard())
    if isinstance(sent, Message):
        settings_views.put(sent, message.from_user.id)


def create_router() -> Router:
    router = Router(name="settings")
    router.callback_query.register(open_settings, F.data == "menu:settings")
    router.callback_query.register(settings_action, F.data.startswith("settings:"))
    router.message.register(cancel_time, Command("cancel"), StateFilter(TimeForm.hour))
    router.message.register(receive_hour, StateFilter(TimeForm.hour))
    return router
