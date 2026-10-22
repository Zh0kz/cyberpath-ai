"""Token-checked Telegram academy navigation and offline quizzes."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import logging
import re
import secrets

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message

from cyberpath_ai.academy.content import Catalog, Lesson
from cyberpath_ai.academy.adaptive import AdaptiveStore
from cyberpath_ai.academy.grading import grade
from cyberpath_ai.academy.storage import AcademyStore
from cyberpath_ai.academy.mission import MissionStore
from cyberpath_ai.news.service import NewsService
from cyberpath_ai.news.storage import NewsStore
from cyberpath_ai.config import Settings
from cyberpath_ai.handlers.navigation import add_navigation
from cyberpath_ai.handlers.ui import safe


CALLBACK = re.compile(r"^ac:([0-9a-f]{8}):([a-z]+)(?::([a-z0-9_]+))?$")
LIFETIME = timedelta(hours=2)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcademyView:
    token: str
    screen: str
    mode: str = "learn"
    module_id: str = ""
    lesson_id: str = ""
    section: int = 0
    attempt_id: int = 0
    question_index: int = 0
    run_id: int = 0
    reason: str = ""
    from_mission: bool = False
    created_at: datetime = datetime.min.replace(tzinfo=UTC)


class AcademyViews:
    def __init__(self) -> None:
        self._views: dict[tuple[int, int, int], AcademyView] = {}
        self._active: dict[tuple[int, int], tuple[int, int, int]] = {}

    def put(self, chat_id: int, message_id: int, user_id: int, view: AcademyView) -> None:
        key = (chat_id, message_id, user_id)
        old = self._active.get((chat_id, user_id))
        if old and old != key:
            self._views.pop(old, None)
        self._views[key] = view
        self._active[(chat_id, user_id)] = key

    def get(self, chat_id: int, message_id: int, user_id: int) -> AcademyView | None:
        key = (chat_id, message_id, user_id)
        view = self._views.get(key)
        if self._active.get((chat_id, user_id)) != key or view is None:
            return None
        if datetime.now(UTC) - view.created_at > LIFETIME:
            self._views.pop(key, None)
            self._active.pop((chat_id, user_id), None)
            return None
        return view

    def current(self, chat_id: int, user_id: int) -> tuple[int, AcademyView] | None:
        key = self._active.get((chat_id, user_id))
        if key is None:
            return None
        view = self.get(*key)
        return (key[1], view) if view else None

    def forget(self, chat_id: int, user_id: int) -> None:
        old = self._active.pop((chat_id, user_id), None)
        if old:
            self._views.pop(old, None)


def _new(screen: str, **kwargs) -> AcademyView:
    return AcademyView(secrets.token_hex(4), screen, created_at=datetime.now(UTC), **kwargs)


def review_view(user_id: int, adaptive: AdaptiveStore) -> AcademyView:
    session_id = adaptive.start_review_session(user_id)
    if session_id is None:
        return _new("review_empty")
    session = adaptive.review_session(user_id, session_id)
    position = session["current_index"]
    item = adaptive.review_items(user_id, session_id)[position]
    run = adaptive.ensure_review_run(user_id, session_id, position)
    return _new("review_question", mode="review", attempt_id=session_id,
                question_index=position, lesson_id=item["lesson_id"], run_id=run["id"])


def _button(token: str, action: str, label: str, param: str | None = None) -> InlineKeyboardButton:
    data = f"ac:{token}:{action}" + (f":{param}" if param is not None else "")
    return InlineKeyboardButton(text=label, callback_data=data)


def _safe(value: str, limit: int = 1200) -> str:
    return safe(value, limit)


def _answer_text(question: dict) -> str:
    if question["kind"] == "choice":
        return next(option["text"] for option in question["options"]
                    if option["id"] == question["answer"])
    if question["kind"] == "text":
        return question["accepted"][0]
    return ", ".join(part["aliases"][0] for part in question["parts"])


def render(view: AcademyView, user_id: int, catalog: Catalog,
           store: AcademyStore, adaptive: AdaptiveStore | None = None
           ) -> tuple[str, InlineKeyboardMarkup]:
    token = view.token
    rows: list[list[InlineKeyboardButton]] = []
    if view.screen == "modules":
        title = "Выберите модуль для квиза" if view.mode == "quiz" else "Выберите модуль для изучения"
        lines = ["<b>📚 CyberPath Academy</b>", title]
        for module in catalog.modules:
            lines.append(f"<b>{_safe(module.title, 120)}</b> — {_safe(module.description, 240)}")
            rows.append([_button(token, "module", module.title, module.id)])
        last = store.last_studied(user_id)
        if last and view.mode == "learn" and catalog.lesson(last[0]):
            rows.append([_button(token, "continue", "▶ Продолжить обучение")])
        if view.mode == "quiz" and store.active_attempt(user_id):
            rows.append([_button(token, "qresume", "▶ Продолжить квиз")])
        if adaptive and view.mode == "learn":
            rows.append([_button(token, "review", "🔁 Повторить темы")])
            rows.append([_button(token, "recommend", "🎯 Следующий урок")])
    elif view.screen == "lessons":
        module = catalog.module_by_id[view.module_id]
        lines = [f"<b>📚 {_safe(module.title, 120)}</b>", "Выберите урок для изучения или проверки."]
        for lesson in module.lessons:
            progress = store.lesson_progress(user_id, lesson.id)
            marker = "★ " if progress and progress["mastered_at_utc"] else (
                "✓ " if progress and progress["completed_at_utc"] else "")
            rows.append([_button(token, "lesson", marker + lesson.title, lesson.id)])
        rows.append([_button(token, "back", "◀️ Назад · К модулям")])
    elif view.screen == "lesson":
        lesson = catalog.lesson(view.lesson_id)
        module = catalog.module_by_id[lesson.module_id]
        position = view.section
        heading = (f"<b>📚 {_safe(module.title, 120)} · {_safe(lesson.title, 150)}</b>\n"
                   f"Раздел {position}/{len(lesson.sections) + 1}")
        if position == 0:
            objectives = "\n".join("• " + _safe(value, 300) for value in lesson.objectives)
            lines = [heading, f"<b>Цели урока</b>\n{objectives}"]
        elif position <= len(lesson.sections):
            section = lesson.sections[position - 1]
            concepts = ", ".join(_safe(value, 70) for value in section["concepts"])
            objective = lesson.objectives[min(position - 1, len(lesson.objectives) - 1)]
            lines = [heading + f" · {position}/{len(lesson.sections)}",
                     f"<b>Цель:</b> {_safe(objective, 280)}",
                     f"<b>{_safe(section['title'], 140)}</b>\n{_safe(section['text'], 1700)}",
                     f"<b>Практический пример</b>\n{_safe(section['example'], 650)}",
                     f"<b>Понятия:</b> {concepts}"]
        else:
            lines = [heading, "<b>Итог урока</b>",
                     _safe(lesson.summary, 1600),
                     "Урок прочитан. Для проверки мастерства пройдите пять вопросов."]
        if position > 0:
            rows.append([_button(token, "prev", "◀ Предыдущий раздел")])
        if position < len(lesson.sections) + 1:
            rows.append([_button(token, "next", "Следующий раздел ▶")])
        else:
            rows.append([_button(token, "qstart", "Начать квиз · 5 вопросов")])
            rows.append([_button(token, "resources", "📚 Дополнительные материалы")])
        rows.append([_button(token, "back", "◀️ Назад · К модулю")])
    elif view.screen == "quiz_intro":
        lesson = catalog.lesson(view.lesson_id)
        lines = [f"<b>🧠 Квиз · {_safe(lesson.title, 150)}</b>",
                 "Пять вопросов. Ответы проверяются по заданным правилам без AI.",
                 "Можно получать до трёх подсказок, открыть ответ или повторить вопрос. История попыток сохраняется."]
        rows.append([_button(token, "qstart", "Начать новый квиз")])
        rows.append([_button(token, "back", "◀️ Назад · К модулю")])
    elif view.screen in {"question", "review_question"}:
        is_review = view.screen == "review_question"
        if is_review:
            if adaptive is None:
                raise ValueError("Review service unavailable")
            items = adaptive.review_items(user_id, view.attempt_id)
            item = items[view.question_index]
            lesson, _, question = catalog.question_by_id[item["question_id"]]
            run = adaptive.review_run(user_id, view.attempt_id, view.question_index)
            heading = f"🔁 Повторение {view.question_index + 1}/{len(items)} · {_safe(lesson.title, 140)}"
            last_question = view.question_index == len(items) - 1
        else:
            lesson = catalog.lesson(view.lesson_id)
            question = lesson.questions[view.question_index]
            run = store.current_run(user_id, view.attempt_id, view.question_index)
            heading = f"🧠 {_safe(lesson.title, 140)} · Вопрос {view.question_index + 1}/5"
            last_question = view.question_index == 4
        if run is None or run["id"] != view.run_id:
            raise ValueError("Outdated question")
        lines = [f"<b>{heading}</b>", _safe(question["prompt"], 1000),
                 f"Подсказки: {run['hints_used']}/3"]
        if run["submitted_answer"] is not None:
            lines.append(f"<b>Балл: {run['points']:.2f}/1</b>" +
                         (" · Ответ показан" if run["revealed"] else ""))
            lines.append(_safe(run["feedback"], 1300))
            lines.append(f"<b>Правильный ответ:</b> {_safe(_answer_text(question), 350)}")
            lines.append(f"Подсказок использовано: {run['hints_used']}/3")
            rows.append([_button(token, "retry", "↻ Повторить вопрос")])
            rows.append([_button(token, "advance", "Результат ▶" if last_question
                                 else "Следующий вопрос ▶")])
        else:
            if question["kind"] == "choice":
                for option in question["options"]:
                    rows.append([_button(token, "choose", option["text"][:60], option["id"])])
            else:
                lines.append("Отправьте короткий ответ сообщением." +
                             (" Разделяйте элементы запятой." if question["kind"] == "parts" else ""))
            if run["hints_used"]:
                hints = "\n".join(f"{index + 1}. {_safe(value, 300)}" for index, value in
                                  enumerate(question["hints"][:run["hints_used"]]))
                lines.append("<b>Подсказки</b>\n" + hints)
            if run["hints_used"] < 3:
                rows.append([_button(token, "hint", f"Подсказка {run['hints_used'] + 1}/3")])
            else:
                rows.append([_button(token, "show", "Показать ответ · 0 баллов")])
        rows.append([_button(token, "back", "◀️ Назад · К урокам")])
    elif view.screen == "review_empty":
        if adaptive is None:
            raise ValueError("Review service unavailable")
        upcoming = adaptive.upcoming(user_id)
        lines = ["<b>🔁 Повторение</b>", "Сейчас нет вопросов, срок повторения которых наступил."]
        if upcoming:
            lines.append("Ближайшее повторение: " + _safe(adaptive.local_due(user_id,
                                                              upcoming["next_due_at_utc"]), 120))
        else:
            lines.append("Сначала завершите хотя бы один вопрос квиза.")
        rows.append([_button(token, "back", "◀️ Назад · К урокам")])
    elif view.screen == "review_results":
        if adaptive is None:
            raise ValueError("Review service unavailable")
        runs = adaptive.review_results(user_id, view.attempt_id)
        lines = ["<b>🔁 Повторение завершено</b>",
                 f"Вопросов: {len(runs)} · Баллы: {sum(row['points'] for row in runs):.2f}/{len(runs)}",
                 "Сроки следующих повторений обновлены по каждому итоговому ответу."]
        rows.append([_button(token, "progress", "Посмотреть прогресс")])
        rows.append([_button(token, "back", "◀️ Назад · К урокам")])
    elif view.screen == "recommended":
        if adaptive is None:
            raise ValueError("Recommendation service unavailable")
        lesson = catalog.lesson(view.lesson_id)
        if lesson:
            lines = [f"<b>Рекомендуем: {_safe(lesson.title, 180)}</b>",
                     _safe(view.reason, 900),
                     "Рекомендация основана на сохранённых ответах и чтении уроков; все уроки доступны свободно."]
            rows.append([_button(token, "recopen", "Открыть урок")])
        else:
            lines = ["<b>Рекомендация</b>",
                     "Все уроки отмечены как прочитанные, и сейчас нет слабых оценённых тем или повторений по сроку."]
        rows.append([_button(token, "back", "◀️ Назад · К урокам")])
    elif view.screen == "skills":
        if adaptive is None:
            raise ValueError("Skill service unavailable")
        lines = [f"<b>📊 Навыки · порог {store.mastery_threshold_percent}%</b>",
                 "Оценка отражает последние итоговые ответы, а не общую гарантию знаний."]
        last_module = None
        for status in adaptive.skill_statuses(user_id):
            if status.skill.module_id != last_module:
                last_module = status.skill.module_id
                lines.append(f"<b>{_safe(catalog.module_by_id[last_module].title, 100)}</b>")
            value = ("Не оценено" if status.percent is None else
                     f"{status.percent}% · {status.assessed}/{status.total} вопросов" +
                     (" · предварительно" if status.assessed < status.total else ""))
            lines.append(f"• {_safe(status.skill.title, 90)}: {value}")
        rows.append([_button(token, "review", "🔁 Повторить темы")])
        rows.append([_button(token, "back", "◀️ Назад · К прогрессу")])
    elif view.screen == "schedule":
        if adaptive is None:
            raise ValueError("Review service unavailable")
        schedules = adaptive.schedules(user_id)
        due = len(adaptive.due_questions(user_id))
        lines = ["<b>🔁 Расписание повторений</b>", f"К повторению сейчас: {due}."]
        if schedules:
            for row in schedules[:8]:
                skill = catalog.skill_for_question.get(row["question_id"])
                title = skill.title if skill else row["question_id"]
                lines.append(f"• {_safe(title, 90)} — {_safe(adaptive.local_due(user_id, row['next_due_at_utc']), 120)}")
        else:
            lines.append("Пока нет расписания. Ответьте на вопросы квиза, чтобы его создать.")
        rows.append([_button(token, "review", "🔁 Начать повторение")])
        rows.append([_button(token, "back", "◀️ Назад · К прогрессу")])
    elif view.screen == "results":
        attempt = store.attempt(user_id, view.attempt_id)
        lesson = catalog.lesson(view.lesson_id)
        points = attempt["total_points"]
        threshold = store.mastery_threshold_percent
        mastered = points / 5 * 100 >= threshold
        lines = [f"<b>🧠 Результат: {_safe(lesson.title, 150)}</b>",
                 f"Баллы: {points:.2f}/5 · {points / 5 * 100:.0f}%",
                 f"Мастерство: {'достигнуто' if mastered else 'пока не достигнуто'} "
                 f"(порог {threshold}%).",
                 "Чтение урока и мастерство учитываются отдельно. Предыдущие попытки сохранены."]
        rows.append([_button(token, "qstart", "↻ Повторить весь квиз")])
        rows.append([_button(token, "resources", "📚 Дополнительные материалы")])
        rows.append([_button(token, "progress", "Посмотреть прогресс")])
        rows.append([_button(token, "back", "◀️ Назад · К модулю")])
    elif view.screen == "progress":
        snapshot = store.progress_snapshot(user_id)
        progress = {row["lesson_id"]: row for row in snapshot["lessons"]}
        mastery = {row["module_id"]: row for row in snapshot["modules"]}
        latest_scores = {}
        for attempt in sorted(snapshot["attempts"], key=lambda row: row["id"], reverse=True):
            latest_scores.setdefault(attempt["lesson_id"], attempt["total_points"])
        lines = [f"<b>📊 Ваш прогресс · порог мастерства {store.mastery_threshold_percent}%</b>",
                 "Чтение, тесты и навыки показаны отдельно."]
        skill_statuses = adaptive.skill_statuses(user_id) if adaptive else []
        if not progress and not snapshot["attempts"]:
            lines.append("Уроки и квизы пока не начаты.")
        for module in catalog.modules:
            completed = sum(bool(progress.get(lesson.id) and progress[lesson.id]["completed_at_utc"])
                            for lesson in module.lessons)
            mastered = mastery[module.id]["mastered_lessons"] if module.id in mastery else 0
            module_skills = [status for status in skill_statuses
                             if status.skill.module_id == module.id]
            assessed_skills = sum(status.percent is not None for status in module_skills)
            strong_skills = sum(status.percent is not None
                                and status.percent >= store.mastery_threshold_percent
                                and status.assessed == status.total for status in module_skills)
            lines.append(f"{_safe(module.title, 120)}: прочитано {completed}/{len(module.lessons)}, "
                         f"квиз ≥{store.mastery_threshold_percent}% когда-либо: "
                         f"{mastered}/{len(module.lessons)}" +
                         (f"; темы с полной оценкой ≥порога: {strong_skills}/{len(module_skills)} "
                          f"(оценено {assessed_skills})" if module_skills else ""))
        answers = snapshot["answers"]
        correct = sum(row["points"] == 1 and not row["revealed"] for row in answers)
        partial = sum(row["points"] is not None and 0 < row["points"] < 1 for row in answers)
        incorrect = sum(row["points"] == 0 for row in answers)
        lines.append(f"Завершённых квизов: {len(snapshot['attempts'])}; ответов: "
                     f"верных {correct}, частичных {partial}, неверных/показанных {incorrect}.")
        if snapshot["attempts"]:
            total = sum(row["total_points"] for row in snapshot["attempts"])
            possible = 5 * len(snapshot["attempts"])
            lines.append(f"Сумма баллов завершённых квизов: {total:.2f}/{possible} "
                         f"({total / possible * 100:.0f}%).")
        lines.append(f"Подсказок: {sum(row['hints_used'] for row in snapshot['runs'])}; "
                     f"показанных ответов: {sum(row['revealed'] for row in snapshot['runs'])}.")
        if adaptive:
            statuses = adaptive.skill_statuses(user_id)
            assessed = [status for status in statuses if status.percent is not None]
            weak = [status for status in assessed if status.percent < store.mastery_threshold_percent]
            if weak:
                weakest = sorted(weak, key=lambda status: status.percent)[:3]
                lines.append("Слабые оценённые темы: " + "; ".join(
                    f"{_safe(status.skill.title, 80)} {status.percent}%" for status in weakest))
            elif assessed:
                lines.append("Оценённых тем ниже порога сейчас нет.")
            else:
                lines.append("Навыки: пока не оценены. Начните квиз одного из уроков.")
            due = adaptive.due_questions(user_id)
            upcoming = adaptive.upcoming(user_id)
            lines.append(f"Вопросов к повторению: {len(due)}." +
                         (" Ближайшая дата: " + _safe(adaptive.local_due(
                             user_id, upcoming["next_due_at_utc"]), 120) if upcoming else ""))
            recommendation = adaptive.recommend(user_id)
            if recommendation:
                lines.append("Следующий урок: " + _safe(recommendation.lesson.title, 120))
            rows.append([_button(token, "skills", "Навыки")])
            rows.append([_button(token, "schedule", "Расписание повторений")])
            rows.append([_button(token, "review", "🔁 Повторить темы")])
            rows.append([_button(token, "recommend", "🎯 Следующий урок")])
        last = snapshot["last_studied"]
        if last and catalog.lesson(last[0]):
            record = progress.get(last[0])
            state = ("освоен" if record and record["mastered_at_utc"] else
                     "прочитан" if record and record["completed_at_utc"] else "в процессе")
            score = (f" · последний квиз {latest_scores[last[0]]:.2f}/5"
                     if last[0] in latest_scores else "")
            lines.append(f"Последний урок: {_safe(catalog.lesson(last[0]).title, 150)} · "
                         f"{state}{score}")
            rows.append([_button(token, "continue", "▶ Продолжить обучение")])
        rows.append([_button(token, "modules", "К урокам")])
    else:
        raise ValueError("Unknown academy screen")
    return "\n\n".join(lines), add_navigation(
        InlineKeyboardMarkup(inline_keyboard=rows),
        back_data="nav:home" if view.screen in {"modules", "progress"} else None,
        back_to="меню")


async def _show(bot: Bot, chat_id: int, message_id: int, user_id: int,
                view: AcademyView, views: AcademyViews, catalog: Catalog,
                store: AcademyStore, adaptive: AdaptiveStore | None = None) -> None:
    text, keyboard = render(view, user_id, catalog, store, adaptive)
    if len(text) > 3900:
        text = "<b>Раздел слишком длинный.</b> Откройте модуль заново."
    previews = LinkPreviewOptions(is_disabled=True)
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                    parse_mode="HTML", reply_markup=keyboard,
                                    link_preview_options=previews)
        views.put(chat_id, message_id, user_id, view)
    except TelegramAPIError:
        try:
            sent = await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard,
                                          link_preview_options=previews)
        except TelegramAPIError as error:
            log.warning("academy_display_failed error_type=%s", type(error).__name__)
            return
        if isinstance(sent, Message):
            views.put(chat_id, sent.message_id, user_id, view)


async def send_academy(bot: Bot, chat_id: int, user_id: int,
                       view: AcademyView, views: AcademyViews, catalog: Catalog,
                       store: AcademyStore, adaptive: AdaptiveStore) -> None:
    """Open an Academy screen from a slash command without editing user messages."""
    text, keyboard = render(view, user_id, catalog, store, adaptive)
    if len(text) > 3900:
        text = "<b>Раздел слишком длинный.</b> Откройте модуль заново."
    try:
        sent = await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard,
                                      link_preview_options=LinkPreviewOptions(is_disabled=True))
        if isinstance(sent, Message):
            views.put(chat_id, sent.message_id, user_id, view)
    except TelegramAPIError as error:
        log.warning("academy_display_failed error_type=%s", type(error).__name__)


async def open_academy(query: CallbackQuery, bot: Bot, catalog: Catalog,
                       academy_store: AcademyStore, academy_views: AcademyViews,
                       adaptive_store: AdaptiveStore, mission_views=None) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != "private":
        await query.answer("Open the academy in a private chat.", show_alert=True)
        return
    if mission_views is not None:
        mission_views.forget(query.message.chat.id, query.from_user.id)
    await query.answer()
    user_id = query.from_user.id
    if query.data == "menu:review":
        view = review_view(user_id, adaptive_store)
    elif query.data == "dash:continue:learn":
        last = academy_store.last_studied(user_id)
        if last and (lesson := catalog.lesson(last[0])):
            view = _new("lesson", module_id=lesson.module_id, lesson_id=lesson.id,
                        section=min(last[1], len(lesson.sections) + 1))
        else:
            view = _new("modules")
    elif query.data == "dash:continue:quiz":
        attempt = academy_store.active_attempt(user_id)
        if attempt and (lesson := catalog.lesson(attempt["lesson_id"])):
            index = attempt["current_index"]
            run = academy_store.ensure_run(user_id, attempt["id"], index)
            view = _new("question", mode="quiz", module_id=lesson.module_id,
                        lesson_id=lesson.id, attempt_id=attempt["id"],
                        question_index=index, run_id=run["id"])
        else:
            view = _new("modules", mode="quiz")
    else:
        screen = "progress" if query.data == "menu:progress" else "modules"
        mode = "quiz" if query.data == "menu:quiz" else "learn"
        view = _new(screen, mode=mode)
    await _show(bot, query.message.chat.id, query.message.message_id, query.from_user.id,
                view, academy_views, catalog, academy_store,
                adaptive_store)


async def academy_action(query: CallbackQuery, bot: Bot, catalog: Catalog,
                         academy_store: AcademyStore, academy_views: AcademyViews,
                         adaptive_store: AdaptiveStore,
                         mission_store: MissionStore | None = None,
                         mission_views=None, news_service: NewsService | None = None,
                         news_store: NewsStore | None = None,
                         settings: Settings | None = None,
                         resource_recommender=None, resource_store=None,
                         resource_views=None) -> None:
    if not isinstance(query.message, Message) or query.message.chat.type != "private":
        await query.answer("Open the academy in a private chat.", show_alert=True)
        return
    match = CALLBACK.fullmatch(query.data or "")
    view = academy_views.get(query.message.chat.id, query.message.message_id, query.from_user.id)
    if not match or view is None or match.group(1) != view.token:
        await query.answer("This academy button is outdated. Open Learn or Quiz again.", show_alert=True)
        return
    action, param = match.group(2), match.group(3)
    user_id = query.from_user.id
    if (action == "resources" and view.screen in {"lesson", "results"}
            and view.lesson_id in catalog.lesson_by_id
            and (view.screen == "results" or
                 view.section == len(catalog.lesson(view.lesson_id).sections) + 1)
            and resource_recommender and resource_store and resource_views):
        from cyberpath_ai.handlers.resources import open_from_academy
        await open_from_academy(query, bot, view, resource_recommender, resource_store,
                                resource_views, academy_views)
        return
    if (action == "back" and view.from_mission and mission_store and mission_views
            and news_service and news_store and settings):
        from cyberpath_ai.handlers.mission import open_mission
        # The mission router owns the overview; it will acknowledge the callback.
        academy_views.forget(query.message.chat.id, user_id)
        await open_mission(query, bot, mission_store, mission_views, news_service,
                           news_store, catalog, academy_views, settings)
        return
    target: AcademyView | None = None
    if action == "modules":
        target = _new("modules", mode="learn" if view.mode == "review" else view.mode)
    elif action == "skills":
        target = _new("skills")
    elif action == "schedule":
        target = _new("schedule")
    elif action == "recommend":
        recommendation = adaptive_store.recommend(user_id)
        target = _new("recommended", lesson_id=recommendation.lesson.id if recommendation else "",
                      reason=recommendation.reason if recommendation else "")
    elif action == "recopen" and view.screen == "recommended":
        lesson = catalog.lesson(view.lesson_id)
        if lesson:
            record = academy_store.lesson_progress(user_id, lesson.id)
            position = min(record["last_section"], len(lesson.sections) + 1) if record else 0
            academy_store.view_section(user_id, lesson, position)
            target = _new("lesson", module_id=lesson.module_id, lesson_id=lesson.id,
                          section=position)
    elif action == "review":
        session_id = adaptive_store.start_review_session(user_id)
        if session_id is None:
            target = _new("review_empty")
        else:
            session = adaptive_store.review_session(user_id, session_id)
            position = session["current_index"]
            item = adaptive_store.review_items(user_id, session_id)[position]
            run = adaptive_store.ensure_review_run(user_id, session_id, position)
            target = _new("review_question", mode="review", attempt_id=session_id,
                          question_index=position, lesson_id=item["lesson_id"], run_id=run["id"])
    elif action == "module" and param in catalog.module_by_id:
        target = _new("lessons", mode=view.mode, module_id=param)
    elif action == "lesson" and catalog.lesson(param or ""):
        lesson = catalog.lesson(param)
        if lesson.module_id != view.module_id or view.screen != "lessons":
            target = None
        elif view.mode == "quiz":
            target = _new("quiz_intro", mode="quiz", module_id=lesson.module_id,
                          lesson_id=lesson.id)
        else:
            academy_store.view_section(user_id, lesson, 0)
            target = _new("lesson", module_id=lesson.module_id, lesson_id=lesson.id)
    elif action == "continue":
        last = academy_store.last_studied(user_id)
        if last and (lesson := catalog.lesson(last[0])):
            target = _new("lesson", module_id=lesson.module_id, lesson_id=lesson.id,
                          section=min(last[1], len(lesson.sections) + 1))
    elif action in {"next", "prev"} and view.screen == "lesson":
        lesson = catalog.lesson(view.lesson_id)
        index = view.section + (1 if action == "next" else -1)
        if lesson and 0 <= index <= len(lesson.sections) + 1:
            academy_store.view_section(user_id, lesson, index)
            if mission_store and index == len(lesson.sections) + 1:
                mission_store.mark_lesson_summary(user_id, lesson.id)
            target = replace(view, token=secrets.token_hex(4), section=index,
                             created_at=datetime.now(UTC))
    elif action == "back":
        if view.screen in {"skills", "schedule"}:
            target = _new("progress")
        elif view.screen in {"question", "review_question", "recommended",
                             "review_empty", "review_results", "lessons"}:
            target = _new("modules", mode="quiz" if view.screen == "question" else
                          view.mode if view.screen == "lessons" else "learn")
        elif view.module_id in catalog.module_by_id:
            target = _new("lessons", mode=view.mode, module_id=view.module_id)
    elif action == "progress":
        target = _new("progress")
    elif (action == "qstart" and view.screen in {"lesson", "quiz_intro", "results"}
          and (view.screen != "lesson" or
               view.section == len(catalog.lesson(view.lesson_id).sections) + 1)):
        lesson = catalog.lesson(view.lesson_id)
        if lesson:
            attempt_id = academy_store.start_attempt(user_id, lesson)
            run = academy_store.ensure_run(user_id, attempt_id, 0)
            target = _new("question", mode="quiz", module_id=lesson.module_id,
                          lesson_id=lesson.id, attempt_id=attempt_id, run_id=run["id"])
    elif action == "qresume":
        attempt = academy_store.active_attempt(user_id)
        if attempt and (lesson := catalog.lesson(attempt["lesson_id"])):
            index = attempt["current_index"]
            run = academy_store.ensure_run(user_id, attempt["id"], index)
            target = _new("question", mode="quiz", module_id=lesson.module_id,
                          lesson_id=lesson.id, attempt_id=attempt["id"],
                          question_index=index, run_id=run["id"])
    elif view.screen in {"question", "review_question"}:
        review = view.screen == "review_question"
        session = (adaptive_store.review_session(user_id, view.attempt_id) if review else
                   academy_store.attempt(user_id, view.attempt_id))
        if session and session["current_index"] == view.question_index and not session["completed_at_utc"]:
            run = (adaptive_store.review_run(user_id, view.attempt_id, view.question_index)
                   if review else academy_store.current_run(user_id, view.attempt_id,
                                                            view.question_index))
            if run and run["id"] == view.run_id:
                if review:
                    items = adaptive_store.review_items(user_id, view.attempt_id)
                    question = catalog.question_by_id[items[view.question_index]["question_id"]][2]
                else:
                    question = catalog.lesson(view.lesson_id).questions[view.question_index]
                if action == "hint":
                    updated = (adaptive_store.review_hint(user_id, view.attempt_id, run["id"])
                               if review else academy_store.use_hint(user_id, view.attempt_id, run["id"]))
                    if updated:
                        target = replace(view, token=secrets.token_hex(4), created_at=datetime.now(UTC))
                elif action == "show":
                    revealed = (adaptive_store.review_reveal(user_id, view.attempt_id, run["id"])
                                if review else academy_store.reveal(user_id, view.attempt_id, run["id"]))
                    if revealed:
                        target = replace(view, token=secrets.token_hex(4), created_at=datetime.now(UTC))
                elif (action == "choose" and question["kind"] == "choice"
                      and param in {option["id"] for option in question["options"]}):
                    result = grade(question, param)
                    submitted = (adaptive_store.review_submit(user_id, view.attempt_id, run["id"],
                                                              param, result) if review else
                                 academy_store.submit(user_id, view.attempt_id, run["id"],
                                                      param, result))
                    if submitted:
                        target = replace(view, token=secrets.token_hex(4), created_at=datetime.now(UTC))
                elif action == "retry":
                    new_run = (adaptive_store.review_retry(user_id, view.attempt_id, run["id"])
                               if review else academy_store.retry_question(user_id, view.attempt_id,
                                                                           run["id"]))
                    if new_run:
                        target = replace(view, token=secrets.token_hex(4), run_id=new_run["id"],
                                         created_at=datetime.now(UTC))
                elif action == "advance":
                    advanced = (adaptive_store.review_advance(user_id, view.attempt_id, run["id"])
                                if review else academy_store.advance(user_id, view.attempt_id, run["id"]))
                    if advanced:
                        if mission_store:
                            mission_store.refresh(user_id)
                        if review:
                            if view.question_index == len(items) - 1:
                                target = _new("review_results", mode="review",
                                              attempt_id=view.attempt_id)
                            else:
                                index = view.question_index + 1
                                new_run = adaptive_store.ensure_review_run(user_id, view.attempt_id,
                                                                           index)
                                target = _new("review_question", mode="review",
                                              lesson_id=items[index]["lesson_id"],
                                              attempt_id=view.attempt_id, question_index=index,
                                              run_id=new_run["id"])
                        else:
                            adaptive_store.record_finalized_quiz_question(
                                user_id, view.attempt_id, view.question_index)
                            if view.question_index == 4:
                                target = _new("results", mode="quiz", module_id=view.module_id,
                                              lesson_id=view.lesson_id, attempt_id=view.attempt_id)
                            else:
                                index = view.question_index + 1
                                new_run = academy_store.ensure_run(user_id, view.attempt_id, index)
                                target = _new("question", mode="quiz", module_id=view.module_id,
                                              lesson_id=view.lesson_id, attempt_id=view.attempt_id,
                                              question_index=index, run_id=new_run["id"])
    if target is None:
        await query.answer("This action is no longer available. Use the current buttons.", show_alert=True)
        return
    if view.from_mission and not target.from_mission:
        target = replace(target, from_mission=True)
    await query.answer()
    await _show(bot, query.message.chat.id, query.message.message_id, user_id,
                target, academy_views, catalog, academy_store, adaptive_store)


async def academy_text(message: Message, bot: Bot, catalog: Catalog,
                       academy_store: AcademyStore, academy_views: AcademyViews,
                       adaptive_store: AdaptiveStore) -> None:
    if message.chat.type != "private" or message.from_user is None or not message.text:
        return
    current = academy_views.current(message.chat.id, message.from_user.id)
    if current is None:
        return
    message_id, view = current
    if view.screen not in {"question", "review_question"} or message.text.startswith("/"):
        return
    if len(message.text) > 200:
        await message.answer("Ответ слишком длинный. Используйте не более 200 символов.")
        return
    if view.screen == "review_question":
        item = adaptive_store.review_items(message.from_user.id, view.attempt_id)[view.question_index]
        question = catalog.question_by_id[item["question_id"]][2]
    else:
        lesson = catalog.lesson(view.lesson_id)
        question = lesson.questions[view.question_index]
    if question["kind"] not in {"text", "parts"}:
        return
    run = (adaptive_store.review_run(message.from_user.id, view.attempt_id, view.question_index)
           if view.screen == "review_question" else
           academy_store.current_run(message.from_user.id, view.attempt_id, view.question_index))
    if run is None or run["id"] != view.run_id or run["submitted_answer"] is not None:
        return
    result = grade(question, message.text.strip())
    submitted = (adaptive_store.review_submit(message.from_user.id, view.attempt_id, run["id"],
                                              message.text.strip(), result)
                 if view.screen == "review_question" else
                 academy_store.submit(message.from_user.id, view.attempt_id, run["id"],
                                      message.text.strip(), result))
    if submitted:
        await _show(bot, message.chat.id, message_id, message.from_user.id,
                    replace(view, token=secrets.token_hex(4), created_at=datetime.now(UTC)),
                    academy_views, catalog, academy_store, adaptive_store)


def create_router() -> Router:
    router = Router(name="academy")
    router.callback_query.register(open_academy,
                                   F.data.in_({"menu:learn", "menu:quiz", "menu:progress",
                                               "menu:review", "dash:continue:learn",
                                               "dash:continue:quiz"}))
    router.callback_query.register(academy_action, F.data.startswith("ac:"))
    router.message.register(academy_text, F.text)
    return router
