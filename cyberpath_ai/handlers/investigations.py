"""Telegram investigation screens with stale-button and user-bound views."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import logging
import re
import secrets

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message

from cyberpath_ai.academy.content import Catalog
from cyberpath_ai.academy.investigations import InvestigationCatalog
from cyberpath_ai.academy.investigation_storage import InvestigationStore
from cyberpath_ai.academy.resources import ResourceCatalog
from cyberpath_ai.handlers.navigation import add_navigation
from cyberpath_ai.handlers.ui import safe


CALLBACK = re.compile(r"^inv:([0-9a-f]{8}):([a-z]+)(?::([a-z0-9_]+))?$")
LIFETIME = timedelta(hours=2)
PREVIEW_OFF = LinkPreviewOptions(is_disabled=True)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InvestigationView:
    token: str
    screen: str
    scenario_id: str = ""
    attempt_id: int = 0
    run_id: int = 0
    created_at: datetime = datetime.min.replace(tzinfo=UTC)


class InvestigationViews:
    def __init__(self) -> None:
        self._views: dict[tuple[int, int, int], InvestigationView] = {}
        self._active: dict[tuple[int, int], tuple[int, int, int]] = {}

    def put(self, chat_id: int, message_id: int, user_id: int, view: InvestigationView) -> None:
        key = (chat_id, message_id, user_id)
        previous = self._active.get((chat_id, user_id))
        if previous and previous != key:
            self._views.pop(previous, None)
        self._views[key] = view
        self._active[(chat_id, user_id)] = key

    def get(self, chat_id: int, message_id: int, user_id: int) -> InvestigationView | None:
        key = (chat_id, message_id, user_id)
        view = self._views.get(key)
        if self._active.get((chat_id, user_id)) != key or view is None:
            return None
        if datetime.now(UTC) - view.created_at > LIFETIME:
            self.forget(chat_id, user_id)
            return None
        return view

    def current(self, chat_id: int, user_id: int) -> tuple[int, InvestigationView] | None:
        key = self._active.get((chat_id, user_id))
        if key is None:
            return None
        view = self.get(*key)
        return (key[1], view) if view else None

    def forget(self, chat_id: int, user_id: int) -> None:
        previous = self._active.pop((chat_id, user_id), None)
        if previous:
            self._views.pop(previous, None)


def _new(screen: str, **kwargs) -> InvestigationView:
    return InvestigationView(secrets.token_hex(4), screen, created_at=datetime.now(UTC), **kwargs)


def _button(view: InvestigationView, action: str, label: str,
            param: str | None = None) -> InlineKeyboardButton:
    data = f"inv:{view.token}:{action}" + (f":{param}" if param else "")
    return InlineKeyboardButton(text=label, callback_data=data)


def render(view: InvestigationView, user_id: int, scenarios: InvestigationCatalog,
           store: InvestigationStore, lessons: Catalog, resources: ResourceCatalog
           ) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list[InlineKeyboardButton]] = []
    if view.screen == "list":
        lines = ["<b>🛡️ Практические задания</b>",
                 "Изучайте вымышленные улики, проверяйте гипотезы и обосновывайте выводы. "
                 "Ответы проверяются локально, без AI и исполнения команд."]
        for scenario in scenarios.scenarios:
            latest = store.latest(user_id, scenario.id)
            state = ("не начато" if latest is None else
                     "завершено" if latest["completed_at_utc"] else
                     f"шаг {latest['current_index'] + 1}/{len(scenario.steps)}")
            lines.append(f"<b>{safe(scenario.title, 100)}</b> · {scenario.difficulty} · {state}\n"
                         f"{safe(scenario.description, 320)}")
            rows.append([_button(view, "intro", scenario.title[:60], scenario.id)])
        rows.append([InlineKeyboardButton(text="◀️ В Академию", callback_data="menu:learn")])
    elif view.screen == "intro":
        scenario = scenarios.by_id[view.scenario_id]
        active = store.active(user_id, scenario.id)
        objectives = "\n".join("• " + safe(item, 180) for item in scenario.objectives)
        lines = [f"<b>🛡️ {safe(scenario.title, 150)}</b>",
                 f"Сложность: {scenario.difficulty} · шагов: {len(scenario.steps)}",
                 safe(scenario.description, 550),
                 "<b>Рекомендуемые знания</b>\n" + "\n".join(
                     "• " + safe(item, 180) for item in scenario.prerequisites),
                 "<b>Цели</b>\n" + objectives,
                 "Каждый шаг можно повторить. Показанный ответ даёт 0 баллов; "
                 "прохождение сохраняется отдельно от квизов."]
        rows.append([_button(view, "start", "▶ Продолжить расследование" if active
                             else "Начать расследование" if store.latest(user_id, scenario.id) is None
                             else "↻ Новая попытка")])
        rows.append([_button(view, "back", "◀️ К заданиям")])
    elif view.screen == "step":
        attempt = store.attempt(user_id, view.attempt_id)
        if attempt is None or attempt["completed_at_utc"] or attempt["scenario_id"] != view.scenario_id:
            raise ValueError("Outdated investigation")
        scenario = scenarios.by_id[view.scenario_id]
        index = attempt["current_index"]
        step = scenario.steps[index]
        run = store.current_run(user_id, view.attempt_id)
        if run is None or run["id"] != view.run_id:
            raise ValueError("Outdated investigation step")
        lines = [f"<b>🛡️ {safe(scenario.title, 140)}</b> · Шаг {index + 1}/{len(scenario.steps)}",
                 f"<b>Цель:</b> {safe(step.purpose, 200)}",
                 f"<b>Улики · вымышленные</b>\n<pre>{safe(step.evidence, 1400)}</pre>",
                 f"<b>Задача</b>\n{safe(step.prompt, 450)}",
                 f"Подсказки: {run['hints_used']}/3"]
        if run["hints_used"]:
            lines.append("<b>Подсказки</b>\n" + "\n".join(
                f"{number + 1}. {safe(hint, 260)}" for number, hint in
                enumerate(step.hints[:run["hints_used"]])))
        if run["submitted_answer"] is not None:
            lines.append(f"<b>Баллы: {run['points']:.1f}/1</b>" +
                         (" · ответ показан" if run["revealed"] else ""))
            lines.append(safe(run["feedback"], 550))
            if run["points"] == 1 or run["revealed"]:
                lines.append("<b>Разбор:</b> " + safe(step.explanation, 700))
                lines.append("<b>Ответ:</b> " + safe(step.answer_display, 260))
            else:
                lines.append("Вы можете повторить шаг; полный разбор также будет в итогах.")
            rows.append([_button(view, "retry", "↻ Повторить этот шаг")])
            rows.append([_button(view, "advance", "Итоги ▶" if index == len(scenario.steps) - 1
                                 else "Продолжить расследование ▶")])
        else:
            if step.kind == "choice":
                for option in step.options:
                    rows.append([_button(view, "choose", option["text"][:60], option["id"])])
            else:
                lines.append("Отправьте короткий текстовый ответ сообщением." +
                             (" Разделите две части запятой." if step.kind == "parts" else ""))
            if run["hints_used"] < 3:
                rows.append([_button(view, "hint", f"Подсказка {run['hints_used'] + 1}/3")])
            else:
                rows.append([_button(view, "show", "Показать ответ · 0 баллов")])
        rows.append([_button(view, "back", "◀️ К описанию")])
    elif view.screen == "debrief":
        attempt = store.attempt(user_id, view.attempt_id)
        if attempt is None or not attempt["completed_at_utc"] or attempt["scenario_id"] != view.scenario_id:
            raise ValueError("Investigation not completed")
        scenario = scenarios.by_id[view.scenario_id]
        latest = {row["step_index"]: row for row in store.runs(user_id, view.attempt_id)}
        correct = [scenario.steps[index].purpose for index, row in latest.items()
                   if row["points"] == 1 and not row["revealed"] and row["hints_used"] == 0]
        assisted = [scenario.steps[index].purpose for index, row in latest.items()
                    if row["points"] == 1 and not row["revealed"] and row["hints_used"] > 0]
        review = [scenario.steps[index].purpose for index, row in latest.items()
                  if row["points"] < 1 or row["revealed"]]
        lines = [f"<b>🛡️ Итоги · {safe(scenario.title, 140)}</b>",
                 f"Баллы: {attempt['total_points']:.1f}/{len(scenario.steps)} · "
                 f"верно без подсказок: {len(correct)}/{len(scenario.steps)}",
                 "<b>Что произошло и какие улики важны</b>\n" + safe(scenario.debrief, 750),
                 "<b>Уверенно разобрано</b>\n" +
                 ("; ".join(safe(item, 100) for item in correct) if correct else "Пока нет."),
                 "<b>Верно с подсказкой</b>\n" +
                 ("; ".join(safe(item, 100) for item in assisted) if assisted else "Нет."),
                 "<b>Ошибки и показанные ответы к повторению</b>\n" +
                 ("; ".join(safe(item, 100) for item in review) if review else "Нет."),
                 "<b>Навыки</b>\n" + ", ".join(
                     safe(lessons.skill_by_id[skill].title, 100) for skill in scenario.skill_ids)]
        for index, step in enumerate(scenario.steps):
            row = latest[index]
            lines.append(f"<b>{index + 1}. {safe(step.purpose, 100)}</b> · {row['points']:.1f}/1\n"
                         f"{safe(step.explanation, 420)}")
        lines.append("<b>Дальше изучить</b> · ссылки на существующие внешние ресурсы:")
        for resource_id in scenario.resource_ids:
            resource = resources.by_id[resource_id]
            lines.append("• " + safe(resource.title, 120))
            rows.append([InlineKeyboardButton(text=resource.title[:55], url=resource.url)])
        rows.append([_button(view, "intro", "↻ Повторить расследование", scenario.id)])
        rows.append([_button(view, "back", "◀️ К заданиям")])
    else:
        raise ValueError("Unknown investigation screen")
    return "\n\n".join(lines), add_navigation(InlineKeyboardMarkup(inline_keyboard=rows))


async def _show(bot: Bot, chat_id: int, message_id: int, user_id: int,
                view: InvestigationView, views: InvestigationViews,
                scenarios: InvestigationCatalog, store: InvestigationStore,
                lessons: Catalog, resources: ResourceCatalog) -> None:
    text, keyboard = render(view, user_id, scenarios, store, lessons, resources)
    if len(text) > 3900:
        text = "<b>Экран слишком длинный.</b> Откройте задание заново."
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
            log.warning("investigation_display_failed error_type=%s", type(error).__name__)


async def open_from_academy(query: CallbackQuery, bot: Bot, academy_views,
                            investigation_views: InvestigationViews,
                            investigation_catalog: InvestigationCatalog,
                            investigation_store: InvestigationStore,
                            catalog: Catalog, resource_catalog: ResourceCatalog) -> None:
    await query.answer()
    academy_views.forget(query.message.chat.id, query.from_user.id)
    await _show(bot, query.message.chat.id, query.message.message_id, query.from_user.id,
                _new("list"), investigation_views, investigation_catalog,
                investigation_store, catalog, resource_catalog)


async def investigation_action(query: CallbackQuery, bot: Bot,
                               investigation_views: InvestigationViews,
                               investigation_catalog: InvestigationCatalog,
                               investigation_store: InvestigationStore,
                               catalog: Catalog, resource_catalog: ResourceCatalog) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != "private":
        await query.answer("Откройте задания в личном чате.", show_alert=True)
        return
    match = CALLBACK.fullmatch(query.data or "")
    view = investigation_views.get(query.message.chat.id, query.message.message_id,
                                   query.from_user.id)
    if match is None or view is None or match.group(1) != view.token:
        await query.answer("Кнопка устарела. Откройте задания снова через Академию.", show_alert=True)
        return
    action, param = match.group(2), match.group(3)
    user_id = query.from_user.id
    target: InvestigationView | None = None
    if action == "intro" and param in investigation_catalog.by_id and view.screen in {"list", "debrief"}:
        target = _new("intro", scenario_id=param)
    elif action == "back":
        target = (_new("intro", scenario_id=view.scenario_id) if view.screen == "step" else
                  _new("list") if view.screen in {"intro", "debrief"} else None)
    elif action == "start" and view.screen == "intro":
        attempt_id = investigation_store.start_or_resume(user_id, view.scenario_id)
        attempt = investigation_store.attempt(user_id, attempt_id)
        run = investigation_store.ensure_run(user_id, attempt_id)
        target = _new("step", scenario_id=view.scenario_id, attempt_id=attempt_id,
                      run_id=run["id"])
    elif view.screen == "step":
        attempt = investigation_store.attempt(user_id, view.attempt_id)
        run = investigation_store.current_run(user_id, view.attempt_id)
        if (attempt and not attempt["completed_at_utc"] and
                attempt["scenario_id"] == view.scenario_id and run and run["id"] == view.run_id):
            step = investigation_catalog.by_id[view.scenario_id].steps[attempt["current_index"]]
            changed = False
            if action == "hint":
                changed = investigation_store.hint(user_id, view.attempt_id, run["id"],
                                                   run["hints_used"]) is not None
            elif action == "show":
                changed = investigation_store.reveal(user_id, view.attempt_id, run["id"])
            elif action == "choose" and step.kind == "choice" and param in {
                    option["id"] for option in step.options}:
                changed = investigation_store.submit(user_id, view.attempt_id, run["id"], param)
            elif action == "retry":
                replacement = investigation_store.retry(user_id, view.attempt_id, run["id"])
                if replacement:
                    target = _new("step", scenario_id=view.scenario_id,
                                  attempt_id=view.attempt_id, run_id=replacement["id"])
            elif action == "advance" and investigation_store.advance(
                    user_id, view.attempt_id, run["id"]):
                after = investigation_store.attempt(user_id, view.attempt_id)
                if after["completed_at_utc"]:
                    target = _new("debrief", scenario_id=view.scenario_id,
                                  attempt_id=view.attempt_id)
                else:
                    next_run = investigation_store.ensure_run(user_id, view.attempt_id)
                    target = _new("step", scenario_id=view.scenario_id,
                                  attempt_id=view.attempt_id, run_id=next_run["id"])
            if changed:
                target = replace(view, token=secrets.token_hex(4), created_at=datetime.now(UTC))
    if target is None:
        await query.answer("Действие больше недоступно. Используйте текущие кнопки.", show_alert=True)
        return
    await query.answer()
    await _show(bot, query.message.chat.id, query.message.message_id, user_id,
                target, investigation_views, investigation_catalog, investigation_store,
                catalog, resource_catalog)


async def investigation_text(message: Message, bot: Bot,
                             investigation_views: InvestigationViews,
                             investigation_catalog: InvestigationCatalog,
                             investigation_store: InvestigationStore,
                             catalog: Catalog, resource_catalog: ResourceCatalog) -> None:
    if message.chat.type != "private" or message.from_user is None or not message.text:
        return
    current = investigation_views.current(message.chat.id, message.from_user.id)
    if current is None or message.text.startswith("/"):
        return
    message_id, view = current
    if view.screen != "step":
        return
    attempt = investigation_store.attempt(message.from_user.id, view.attempt_id)
    run = investigation_store.current_run(message.from_user.id, view.attempt_id)
    if (attempt is None or attempt["completed_at_utc"] or
            attempt["scenario_id"] != view.scenario_id or run is None or
            run["id"] != view.run_id or run["submitted_answer"] is not None):
        return
    step = investigation_catalog.by_id[view.scenario_id].steps[attempt["current_index"]]
    if step.kind not in {"text", "parts"}:
        return
    answer = message.text.strip()
    if not 0 < len(answer) <= 250:
        await message.answer("Ответ должен содержать от 1 до 250 символов.")
        return
    if investigation_store.submit(message.from_user.id, view.attempt_id, run["id"], answer):
        await _show(bot, message.chat.id, message_id, message.from_user.id,
                    replace(view, token=secrets.token_hex(4), created_at=datetime.now(UTC)),
                    investigation_views, investigation_catalog, investigation_store,
                    catalog, resource_catalog)


def create_router() -> Router:
    router = Router(name="investigations")
    router.callback_query.register(investigation_action, F.data.startswith("inv:"))
    return router
