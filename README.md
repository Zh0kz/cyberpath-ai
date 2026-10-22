# CyberPath AI

A private-by-default cybersecurity education Telegram bot. **Stages 1 through 4.4A and 4.4C are implemented.** `/start` and `/menu` show a learning dashboard with News, Academy, Quiz, Daily Mission, Review, Progress, Settings, and Help. News reads real public feeds and stores source-backed entries in SQLite. Settings controls an opt-in daily news digest. Each article can also receive an optional Russian educational explanation through OpenRouter or OpenAI. The Academy offers curated Russian lessons, offline quizzes, topic assessment, manual spaced reviews, and offline external-resource recommendations. There is no active scanning.

## Requirements

- Python 3.12 or newer (the local development environment uses Python 3.13)
- A Telegram bot token from BotFather
- Your numeric Telegram user ID
- A running bot process for daily delivery (no external scheduler or server required)

## Launch on Windows CMD

From the project directory:

```cmd
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
copy .env.example .env
```

Use `.env` as a private reference for your values. The app reads environment variables directly and does **not** load `.env` automatically. Set the required variables in the current CMD window, then start it:

```cmd
set "TELEGRAM_BOT_TOKEN=your-real-bot-token"
set "ALLOWED_USER_IDS=your-numeric-telegram-id"
.venv\Scripts\python.exe -m cyberpath_ai
```

Optional news settings can be set with the same `set "NAME=value"` syntax. Stop the bot with `Ctrl+C`.

To verify it, message your bot `/start` and tap **📰 Новости**. The screen shows **one article**, marked `1/N` (up to five total), or an empty/failure state. Use **Далее** and **Назад**, including at the ends, then **Открыть источник**. **Обновить новости** requests new data after the cooldown. Telegram link previews are disabled. The SQLite file appears at `data\cyberpath.sqlite3` by default. A Telegram account outside `ALLOWED_USER_IDS` receives no bot response.

For daily news, tap **⚙️ Настройки** in a private chat. Notifications begin disabled. Tap **Включить новости**, choose **3 статьи** or **5 статей**, and use **Изменить время (HH)** to send a two-digit local hour such as `09` or `18`; minutes are always `00`. `/cancel` or **Отменить ввод времени** stops time entry. The settings panel shows your stored timezone, time, count, and opt-in status. **Отправить тестовый выпуск** delivers a separate test message without enabling or consuming that day's scheduled delivery. Leave the bot process running for automatic sends. Neither the scheduler nor test digest needs an OpenRouter or OpenAI key.

For the Academy, tap **📚 Обучение**, select a module and lesson, then use **Следующий раздел** through the objectives, three lesson sections, and summary. The summary marks reading complete and offers a five-question quiz. **Предыдущий раздел** returns to earlier sections; **Назад** returns to the lesson list. **Продолжить обучение** restores the last viewed section, including after restarting the process. **🧠 Тесты** opens the same modules for direct assessment and offers **Продолжить квиз** for an unfinished attempt. Answer multiple-choice questions with buttons and text questions with a short Telegram message. Each question offers three successive hints. **Показать ответ** appears after the third hint and gives zero points for that question run; **Повторить вопрос** starts a new run while preserving the previous one. **📊 Прогресс** shows reading completion, mastery, attempts, answer counts, hints, and reveals. The Academy works with both AI keys empty.

For free-model AI explanations, set OpenRouter in that CMD window:

```cmd
set "AI_PROVIDER=openrouter"
set "OPENROUTER_API_KEY=your-openrouter-api-key"
set "OPENROUTER_MODEL=openrouter/free"
.venv\Scripts\python.exe -m cyberpath_ai.ai.check_model
.venv\Scripts\python.exe -m cyberpath_ai
```

Open a news item and tap **🧠 Объяснить новость**. The bot performs a provider preflight before its first generation request, then shows a loading state and a Russian explanation. Choose Beginner, Intermediate, or Advanced; **◀️ Вернуться к новости** restores the original article view. The original source remains linked. Missing credentials or provider failures do not interrupt News navigation. The `check_model` command reports the provider, model, credential presence, availability status, and compatibility issues without generating text. All automated tests mock AI requests.

To diagnose a live explanation in Windows CMD, keep the bot CMD window visible and tap **🧠 Объяснить новость** on one article. Find its `ai_explanation_result` JSON line. A successful line shows `outcome=success`; a failure line shows `phase` and `failure_category`. `actual_model` identifies the model selected by `openrouter/free` when the provider supplies it. If `finish_reason` is `length`, you may restart with a larger budget such as `set "AI_MAX_OUTPUT_TOKENS=1800"`. A failed explanation is not cached; tap the same level again for a manual retry. Each attempt consumes one of the daily `MAX_AI_CALLS_PER_DAY` reservations. The offline tests above do not contact OpenRouter or OpenAI.

To switch to OpenAI later, restart the bot from a CMD window with `AI_PROVIDER=openai`, `OPENAI_API_KEY` and `OPENAI_MODEL=gpt-6-luna` set. The unselected provider's key is not needed. OpenAI generation may incur charges; the bot never switches providers or models automatically.

Run the offline test suite with:

```cmd
.venv\Scripts\python.exe -m pytest -q
```

## Development and GitHub CI

Install from `pyproject.toml` using the Windows CMD commands above; there is no `requirements.txt`. Tests use temporary SQLite databases, remove bot and AI credentials from their environment, and reject unexpected external network connections. The GitHub Actions workflow runs the complete pytest suite on Python 3.13 for pushes and pull requests. It supplies no live bot or AI credentials, and it does not start the bot or call paid APIs. Dependency installation still downloads public Python packages.

Keep your actual bot token, user IDs, and AI keys in your private CMD environment or `.env` reference file. The app does not load `.env` automatically. Commit `.env.example` only with placeholder values. Git ignores `.env` variants, `data/`, `backups/`, SQLite files and their sidecars, logs, virtual environments, caches, and temporary files. Before your first commit, inspect the files Git would add with `git add --dry-run .`; after staging, inspect `git diff --cached --name-only` and `git diff --cached` without sharing any secret values. If a credential ever reaches Git history, rotate it before publishing and remove it from that history; adding it to `.gitignore` alone cannot erase an earlier commit.

For content or code changes, run `.venv\Scripts\python.exe -m pytest -q` before committing. The workflow file is `.github/workflows/tests.yml`. No GitHub Actions secrets are needed for the tests.

To create a separate private GitHub repository later, install [GitHub CLI](https://github.com/cli/cli/blob/trunk/docs/install_windows.md) with `winget install --id GitHub.cli --source winget`, then reopen CMD. From this project directory, review Git's candidate files before committing:

```cmd
git add --dry-run .
git check-ignore -v .env data\cyberpath.sqlite3 backups\cyberpath-before-stage4-2.sqlite3
git add .
git diff --cached --name-only
git diff --cached --check
git commit -m "Initial CyberPath AI version"
gh auth login
gh repo create cyberpath-ai --private --source=. --remote=origin --push
```

The local Git repository is initialized on `main`, with no commit or remote added by this preparation. Choose another repository name in the last command if `cyberpath-ai` is already taken. Confirm `git diff --cached --name-only` contains only source, documentation, tests, and the placeholder `.env.example` before committing.

## News sources and behavior

- [CISA's official KEV GitHub mirror](https://github.com/cisagov/kev-data) supplies CVE IDs, vulnerability names, short descriptions, and `dateAdded`. KEV has **date precision only**. The displayed link goes to the [official CISA catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog).
- [NVD CVE API 2.0](https://nvd.nist.gov/developers/vulnerabilities) supplies recently published CVEs, descriptions, and publication timestamps. Items link to their official NVD detail pages. Requests use the publication date window and at most the configured number of pages, with a pause between pages. No NVD API key is needed for this MVP.
- [BleepingComputer News RSS](https://www.bleepingcomputer.com/rss-feeds/) supplies article titles, descriptions, dates, and original article links.

Source timestamps are normalized to UTC. A KEV date is stored as that day's UTC boundary and presented only as a date; an entry is eligible while that calendar day overlaps the lookback window. Items lacking a usable source date, title, or HTTP(S) URL are skipped. News is ordered by most recent calendar day, then source relevance and recency within that day. Exact duplicate article URLs are normalized by removing fragments and tracking parameters. CISA and NVD records are deduplicated by their source plus CVE ID. Separate articles about the same CVE remain separate.

The bot stores source URLs, canonical URLs, titles, descriptions, CVE IDs, and normalized dates in SQLite. Normal menu opens reuse a successful cache for 30 minutes by default. Refresh bypasses that cache but respects a 60-second cooldown. Connection failures, timeouts, invalid feeds, and HTTP rate limits affect only the failed source; cached recent items remain available. An empty result is shown only when no eligible items are available. NVD pagination is capped to protect the unauthenticated public API; if the chosen window exceeds the cap, some NVD results may be omitted. All automated tests use mocked responses; a live Telegram and feed run is a separate local verification step.

## Stage 2.1 news view and diagnostics

The article view uses escaped HTML for source text. Its headline, source and UTC date, and concise **Source description** remain based on the feed; the layout keeps any future Russian explanation separate from the original source text and English technical terms. The original URL is available through an inline button. A view keeps a short-lived snapshot for navigation. Buttons from a replaced view, a prior bot run, or more than two hours ago show an expiry message. When Telegram rejects an edit, the bot tries a new message. A refresh during the configured cooldown shows the remaining wait and cached items.

The running CMD window now receives JSON logs for each CISA KEV, NVD, and BleepingComputer fetch. A record includes `timestamp_utc`, `source`, `outcome`, `http_status` when known, `articles_retrieved`, and a safe `error_type` such as `timeout`, `http_error`, or `rate_limited`. A 429 response is followed by a per-source backoff using `Retry-After` when supplied, otherwise 60 seconds; NVD pages also remain spaced apart. Cache reuse logs its reason and the source names from the last failed attempt. Logs omit bot tokens, raw URLs, response bodies, and exception messages.

If Telegram says a source is temporarily unavailable, look for `news_source_fetch` records in CMD. A `failure` record identifies which source failed and, when available, its HTTP status; a `skipped` record means a 429 backoff is still active. `news_cache_used` explains when an earlier failure is being shown from cache. Earlier fetches did not record HTTP status or error type, so their precise causes cannot be reconstructed from SQLite alone.

## Stage 2.2 AI explanations

The `check_model` command above performs a read-only provider check without generating text. For `openrouter/free`, it verifies credentials through `/api/v1/key`; a virtual router's actual model availability is established only by generation.

The bot sends only the selected article's stored source, title, description, UTC date, CVE IDs, and original URL. It instructs the model to treat this data as untrusted, separate incident facts from general concepts, mark any example hypothetical, avoid unsupported incident details, and provide only defensive guidance. No browsing, plugins, or other external tools are enabled. Beginner displays six concise Russian sections; Intermediate and Advanced add more detail and may include a labeled hypothetical example. All text is escaped for Telegram HTML; long responses are shortened to fit a message. AI text should still be checked against the original source when precision matters.

Completed explanations are stored in the same SQLite database, keyed by article identity, a hash of its source content, language, level, provider, and model. Existing OpenAI cache entries from Stage 2.2 remain readable. Reopening the same explanation uses cached text without another generation. The first uncached request performs a read-only provider check. Daily request reservations are persisted and capped by UTC day; failed generation attempts count toward the cap. The SDK has retries disabled, a timeout is configured, and an in-process semaphore limits concurrent requests. Errors are summarized without logging API keys or raw exception messages. Leaving the selected provider's key empty keeps News available and disables new explanation generation.

## Stage 2.3 provider selection

`AI_PROVIDER=openrouter` is the environment default. OpenRouter uses the official OpenAI Python SDK at `https://openrouter.ai/api/v1` with Chat Completions. Its default and only automatically selected model is `openrouter/free`; the bot does not use provider or model fallbacks. Because routed free models differ in structured-output support, this adapter does not request `response_format`. It asks for six short, delimited sections and validates those locally; validated JSON and Markdown-fenced JSON are also accepted from the same response. Intermediate and Advanced require a labeled hypothetical example. Empty, truncated, malformed, or incomplete responses produce an error and are never cached or automatically retried. `openrouter/free` is a virtual free router, so the preflight checks `/api/v1/key` rather than requiring fixed-model metadata. The `check_model` output says `Model available: virtual router; checked when generating`; this does not mean the model is unavailable. A custom OpenRouter model is used only when explicitly set through `OPENROUTER_MODEL` and may be billable. Custom `:free` slugs require zero pricing in model metadata before generation.

`AI_PROVIDER=openai` keeps the Stage 2.2 Responses API path with strict JSON schema. The model defaults to `gpt-6-luna`. The same cache, daily cap, timeout, and concurrency settings apply to either provider. Authentication, model access, compatibility, timeout, rate limit, and invalid response failures have distinct safe messages. The optional `check_model` command performs no generation request; it may make a read-only network request when credentials are present. A successful credential check does not guarantee that a later free route or JSON explanation will succeed.

The running CMD window emits `ai_explanation_result` JSON records for generation results. Each record includes the selected provider, configured and actual returned model when supplied, finish reason, content length and empty/truncated flags, token usage when supplied, and success or a failure phase/category. Prompts, response text, API keys, and raw provider errors are not logged. A `truncated` category with `finish_reason=length` means the output hit its token budget; increase `AI_MAX_OUTPUT_TOKENS` only if needed. `json_parse`, `plain_text_parse`, or `missing_sections` indicates formatting/validation failure from the routed model. The current 1,400-token default is retained with a shorter prompt and output format; you may configure a higher cap. Failed attempts count against the daily quota. The explanation button remains available for manual retry after an error.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | required | Telegram bot token; never logged |
| `ALLOWED_USER_IDS` | required | Comma-separated positive Telegram user IDs |
| `NEWS_DB_PATH` | `data/cyberpath.sqlite3` | SQLite database path |
| `NEWS_LOOKBACK_HOURS` | `72` | Recent-news window; maximum 2,880 hours (NVD's 120-day limit) |
| `NEWS_CACHE_MINUTES` | `30` | Successful-fetch cache lifetime |
| `NEWS_REFRESH_COOLDOWN_SECONDS` | `60` | Minimum time between fetch attempts |
| `NEWS_HTTP_TIMEOUT_SECONDS` | `12` | Per-request HTTP timeout |
| `NEWS_NVD_MAX_PAGES` | `2` | Maximum NVD pages per fetch, 2,000 records per page |
| `AI_PROVIDER` | `openrouter` | `openrouter` or `openai`; selects exactly one provider |
| `OPENROUTER_API_KEY` | unset | Required for OpenRouter explanations; never logged |
| `OPENROUTER_MODEL` | `openrouter/free` | OpenRouter model; custom models are used only when explicitly configured |
| `OPENAI_API_KEY` | unset | Required for OpenAI explanations; never logged |
| `OPENAI_MODEL` | `gpt-6-luna` | OpenAI Responses model |
| `AI_TIMEOUT_SECONDS` | `30` | Selected provider request timeout |
| `AI_MAX_OUTPUT_TOKENS` | `1400` | Maximum response tokens per explanation |
| `MAX_AI_CALLS_PER_DAY` | `10` | Maximum uncached generation attempts per UTC day, shared across providers |
| `AI_CONCURRENCY_LIMIT` | `2` | Maximum simultaneous AI requests in this bot process |
| `MASTERY_THRESHOLD_PERCENT` | `80` | Quiz score needed to mark a lesson mastered; reading completion is separate |
| `REVIEW_FAILURE_INTERVAL_DAYS` | `1` | Days until another review after an incorrect, partial, hinted or revealed answer |
| `REVIEW_SUCCESS_INTERVALS_DAYS` | `3,7,14,30` | Days after an independent full-credit answer and successive successful reviews |
| `REVIEW_SESSION_SIZE` | `5` | Maximum due questions per manual review session (1–10) |

## Stage 4.1 Academy

The packaged JSON catalog lives in `cyberpath_ai/academy/lessons/`. It has three modules, three lessons each, and five curated questions per lesson: **45 questions total**. Networking covers IP Addressing and Subnets, TCP vs UDP, and DNS and HTTP/HTTPS. Linux covers Basic Linux Commands, Linux File Permissions, and Users, Processes and Logs. Web Security covers Authentication and Sessions, SQL Injection Fundamentals, and Cross-Site Scripting (XSS). Content uses Russian beginner explanations, English technical terms, authorized lab examples, and defensive recommendations. Relevant reference material includes [RFC 1918](https://www.rfc-editor.org/rfc/rfc1918), [TCP RFC 9293](https://www.rfc-editor.org/rfc/rfc9293), [UDP RFC 768](https://www.rfc-editor.org/rfc/rfc768), [Linux man pages](https://man7.org/linux/man-pages/), and [OWASP cheat sheets](https://cheatsheetseries.owasp.org/).

`content.py` validates IDs, objectives, sections, answer keys, three distinct hints, and exactly five questions per lesson at startup. To add a lesson, add an object to one of the module JSON files with a unique ID, objectives, at least three sections (`title`, `text`, `example`, `concepts`), a summary, and exactly five questions. Questions can be `choice` (three to five options and an answer option ID), `text` (explicit accepted equivalents), or `parts` (two or three named components whose points total 1.0, aliases, and a wrong-entry penalty). Every question requires an explanation, three hints, and feedback for wrong answers. Run the offline tests after editing content; an invalid catalog prevents startup with a content error. Keep examples non-destructive and for authorized environments.

`grading.py` normalizes Unicode, case, spacing, and common punctuation before matching whole accepted answers. Component questions award only their declared points and penalize unrecognized extra entries. It does not use substring matching or AI. `storage.py` keeps reading progress, immutable question-run history, attempts, hint/reveal counts, and module mastery in the existing SQLite file. Retrying a question creates a new run, and the latest run for each question determines the attempt score; all earlier runs remain visible in history. Completing a lesson summary records reading completion independently of the quiz threshold. The Progress panel aggregates the learner's own records by Telegram user ID.

Academy buttons carry short-lived view tokens. A button from an earlier view or process asks the learner to reopen Learn or Quiz. The bot escapes lesson text for Telegram HTML, disables link previews, and keeps views within message limits. A failed message edit falls back to a new message. The global Telegram allowlist still blocks unauthorized updates before any Academy handler runs. The Academy makes no HTTP or AI requests.

## Stage 4.2 adaptive learning and review

The curated `cyberpath_ai/academy/skills.json` maps all 45 stable question IDs to 18 skills. Keep those IDs unchanged when editing lessons; add a skill mapping for every new question. Startup validation rejects duplicate, unknown, or unmapped question IDs. **Skill percentages describe observed answers, not guaranteed real-world proficiency.** For each question, only its latest finalized quiz or review answer is used. Its effective score is `points × (1 − 0.15 × hints used)`; Show Answer always scores `0`. The skill percentage is the mean effective score across assessed questions in that skill, multiplied by 100 and rounded to a whole percent. A skill with no finalized answer displays **Not assessed**, not 0%. The 80% threshold applies to the displayed skill assessment, while Stage 4.1 reading completion and lesson quiz mastery remain separate. Retrying a question replaces that question's evidence only when the learner advances; older answer runs remain in SQLite history. Repeated taps cannot create extra evidence.

Each finalized question receives its own UTC review schedule. An independent full-credit quiz answer is due in 3 days. Independent full-credit reviews then move to 7, 14, and 30 days, staying at 30 on further successes. A partial, incorrect, hinted, or revealed final answer resets the streak and is due after 1 day. Repeating a lesson quiz does not accelerate the review ladder. The intervals, session size, and failure delay are configurable with the variables above. The algorithm uses stored timestamps and can be tested with an injected clock; due dates are shown in the user's saved IANA timezone from Settings, or `DEFAULT_TIMEZONE` for a user without saved preferences.

**🔁 Review Weak Topics** appears in Learn and Progress. It selects up to five due, distinct curated questions, prioritizing lower measured skill scores and then older due dates. Reviews use the same deterministic answers, accepted equivalents, partial credit, three hints, Show Answer, and retry behavior as quizzes. One active review session per user is stored in SQLite; reopening the button after a process restart resumes it. The final run of each review question updates its skill evidence and next due date exactly once when the learner advances. If nothing is due, the bot shows the next local review date or explains how to create a schedule.

**🎯 Recommended Next Lesson** explains the choice. The engine considers due reviews first, then two or more recent mistakes or hint-dependent answers within a lesson, then measured skills below the mastery threshold, then an unread lesson. If a suggested lesson has an unread earlier lesson in its module, the earlier lesson is recommended as a prerequisite. These are suggestions only; all lessons remain available. Progress now includes completed lessons, aggregate quiz points, weakest assessed skills, the count of due reviews, the next review date, and the recommended lesson. For each module it shows both historical lesson quizzes that ever reached 80% and current skill assessments that reach the threshold with every tagged question assessed. A later failed review can lower the current skill score without erasing a completed quiz milestone. **Навыки** and **Расписание повторений** open detail views.

Migration is additive: `skill_evidence`, `review_schedules`, `review_sessions`, `review_items`, and `review_runs` are created in the same SQLite database. No Stage 4.1 rows are deleted or reset. On startup, finalized answers from existing completed quizzes and advanced questions of interrupted quizzes are imported in timestamp order. Only the last answer run per question in an attempt counts; unique source keys make imports idempotent. A newer review schedule is never overwritten by an older imported answer. Existing unanswered questions are not treated as failures or scheduled. Keep a normal backup of the SQLite file before upgrades, as with any local database.

To verify manually in Windows CMD, install and run the bot with the commands above; AI keys may remain empty. Complete a quiz question independently and open **Progress → Расписание повторений** to see its local due date. Use three hints and **Показать ответ** on another question; its review will be scheduled sooner and will not count as demonstrated mastery. **Learn → 🎯 Recommended Next Lesson** shows a reason. Once a due date arrives, use **Learn → 🔁 Review Weak Topics**; answer a review, restart the bot in the middle of a second review, and tap the same button to resume. No review notification is sent automatically.

To verify manually on Windows CMD after installation, run `.venv\Scripts\python.exe -m pytest -q`, set `TELEGRAM_BOT_TOKEN` and `ALLOWED_USER_IDS` in the current CMD window as shown above, and run `.venv\Scripts\python.exe -m cyberpath_ai`. In a private bot chat: `/start` → **Learn** → **Networking Fundamentals** → **IP Addressing and Subnets** → read through **Итог** → start the quiz. Try an incorrect choice and inspect the explanation; use three hints on another question, reveal the answer, then retry it. Open **Progress** and confirm reading and answer history. Restart the bot and use **Continue Learning** or **Continue Quiz** to check SQLite persistence. No AI key is needed for these steps.

## Stage 3 daily news delivery

The async scheduler starts with the polling bot and stops with it. It calculates each enabled user's next local delivery from their saved timezone and hour. New users default to `Asia/Almaty`, `09:00`, three articles, and **disabled**. `DEFAULT_TIMEZONE` and `DAILY_DIGEST_TIME` change defaults for newly created preferences only; existing SQLite preferences stay intact. The hour is editable through Settings; the timezone is displayed but currently set from the default when the preference is created. The `tzdata` dependency supplies IANA timezones on Windows. The process must remain running at the delivery time. Restarting after a missed time schedules the next future occurrence, without sending missed days in bulk.

Private chat IDs are saved only after an allowed user interacts with the bot in a private chat. The scheduler loads only enabled preferences for currently allowed user IDs. Each scheduled attempt has a unique `(user ID, local date)` SQLite claim, so a restart or changed time cannot send a second scheduled digest that day. Delivery history records `reserved`, `sending`, `sent`, `failed`, or `unknown`. A Telegram 429 may receive one bounded retry; an uncertain connection failure is recorded as `unknown` and is not retried automatically, because Telegram offers no idempotency key for messages. This favors avoiding duplicate messages, although an uncertain send may be missed. Manual **Send Test Digest Now** uses a separate history entry and does not affect scheduled uniqueness.

The digest uses the existing news service and shows up to the selected three or five source-backed articles, with source dates and original URL buttons. It escapes source text and disables link previews. Recent stored articles are labeled cached when reused; if sources fail and no recent items remain, the bot may show clearly labeled older cached articles from the past seven days. When no suitable item exists, it sends a short informational message. The scheduler never calls an AI provider. Source failures and Telegram delivery failures have separate safe log events.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_TIMEZONE` | `Asia/Almaty` | IANA timezone for newly created daily preferences |
| `DAILY_DIGEST_TIME` | `09:00` | Initial daily hour for new users; must be `HH:00` |

## Stage 4.3 global navigation and daily missions

Every Academy, News, AI explanation, Settings, and mission screen has **🏠 Главное меню**; `/menu` and `/start` clear temporary input and return to the main menu. **◀️ Назад** returns to a logical parent screen. This navigation does not erase lesson position, quiz attempts, hints, answer history, or review sessions. Short-lived old callback buttons are rejected. Continue Learning and Continue Quiz remain available from Academy after returning home.

**🎯 Daily Mission** creates one SQLite snapshot per allowed user and local calendar date. It uses the saved timezone, or `DEFAULT_TIMEZONE` (`Asia/Almaty`) when no preference exists. A timezone change keeps today's existing mission until the local date changes. The snapshot stores up to three recent, original-source news IDs, one lesson from the existing adaptive recommender, that lesson's five curated question IDs, and any due review question IDs. If there are no due reviews or measured weak skills, an unfinished quiz or partially read lesson takes priority so the learner can continue. The mission displays the reason for the lesson selection; unassessed topics are never described as weak. News collection uses the existing free source service and its cache. If no suitable fresh news exists, the news task is omitted and the screen says so. If no reviews are due, review is marked **not required today**. The mission does not send an automatic notification and works without AI credentials.

Opening a suggested article records **viewed in the bot**, not comprehension. Reaching the selected lesson's summary records reading completion for the mission, not mastery. The quiz task completes only after an actual five-question attempt for that lesson is finished on the mission's local date; Show Answer may finish a quiz but never scores as an independent correct answer. Review completes when each selected due question has finalized review evidence for the local date. Task and whole-mission timestamps are written once, so reopening or repeated taps cannot create duplicate completion events. The snapshot and progress survive a bot restart. Mission buttons lead into the existing Academy lesson, quiz, and review screens; Back returns to the mission. The optional **🎯 Today's Mission** button in an opt-in daily news digest opens the same mission without creating another scheduled message.

The database migration adds `daily_missions` and `mission_news_views` tables to the existing SQLite file. Existing Academy, news, and delivery tables remain in place. Mission news and quiz choices stay fixed for that user and local date. A mission is created when first opened, so an article published later that day appears in normal News but does not silently change the mission. Telegram's allowlist still applies to all mission actions.

To test and run on Windows CMD from the project folder:

```cmd
cd /d "C:\Users\User\Desktop\tg bot"
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
.venv\Scripts\python.exe -m pytest -q
set TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
set ALLOWED_USER_IDS=YOUR_NUMERIC_TELEGRAM_ID
set DEFAULT_TIMEZONE=Asia/Almaty
.venv\Scripts\python.exe -m cyberpath_ai
```

Keep `OPENROUTER_API_KEY` and `OPENAI_API_KEY` unset for this check. In a private Telegram chat, send `/start`, open **🎯 Daily Mission**, open a listed news article, return to the mission, and use the lesson, quiz, and review buttons. Send `/menu` during a quiz text answer and reopen the mission to confirm the attempt remains. Restart the process and reopen today's mission to confirm the same selections and completed task indicators. Open **Settings → Send Test Digest Now** to see the optional mission button; the test digest does not enable scheduled delivery. If today's news is unavailable, the mission still offers learning and the quiz.

## Stage 4.4A native menu and interface

At startup the bot registers Telegram's built-in command list and `MenuButtonCommands` using `set_my_commands()` and `set_chat_menu_button()`. These calls only configure Telegram's native menu; CyberPath does not install a permanent reply keyboard. Registration failures are logged by exception type and do not stop polling. The allowlist applies to every command and callback.

| Command | Opens |
| --- | --- |
| `/start`, `/menu` | Learning dashboard with saved reading, review, mission, and quiz status |
| `/news` | Recent source-backed news |
| `/learn` | Academy modules |
| `/quiz` | Five-question quiz selection and unfinished attempt |
| `/mission` | Today's saved mission |
| `/review` | Due review questions or an empty-state explanation |
| `/progress` | Saved Academy progress |
| `/settings` | Daily-news preferences |
| `/help` | Short beginner guide |

Commands work while a text answer or delivery-hour input is expected. They exit the temporary screen without submitting the command as an answer or changing the saved hour. The dashboard reads completed lessons, due reviews, today's mission status, the recommended lesson, and unfinished lesson or quiz state from SQLite. It never presents an unassessed skill as 0%. Inline buttons are grouped in pairs; Home and Back controls remain available throughout the bot. The lesson and quiz screens now show clearer titles, section/question progress, objective or prompt, hints, feedback, and next actions. News titles and descriptions remain source-backed and escaped for Telegram HTML. The bot cannot change Telegram's client colors, fonts, or button shapes.

On Windows CMD, from the project folder, run `.venv\Scripts\python.exe -m pytest -q`, set `TELEGRAM_BOT_TOKEN` and `ALLOWED_USER_IDS` in that CMD window, then run `.venv\Scripts\python.exe -m cyberpath_ai`. In a private chat, open Telegram's bot command menu and try `/start`, `/news`, `/learn`, `/quiz`, `/mission`, `/review`, `/progress`, `/settings`, and `/help`. Start a quiz, send `/menu` during a text question, and reopen **Продолжить квиз** to verify that the answer was not submitted and the attempt remains. In Settings, start changing the hour, send `/help`, and confirm the saved hour is unchanged. Use **🏠 Главное меню** and **◀️ Назад** from several screens. An unauthorized Telegram user should receive no response.

## Stage 4.4C personalized learning resources

The offline catalog at `cyberpath_ai/academy/resources.json` contains **16 verified entries** across videos, labs, reading, and further practice. Every entry has a stable ID, exact HTTPS URL, Russian description, existing lesson and skill IDs, difficulty, language, access status, optional verified duration, prerequisites, objectives, a rationale, and a verification date. The bot never fetches these sites at runtime. The catalog loader rejects missing or duplicate IDs, duplicate or unsafe URLs, unknown lesson/skill references, mismatched skill-to-lesson mappings, and invalid categories, dates, levels, or durations. The packaged JSON is included in installed distributions. To add a resource, inspect its exact official page and access terms, add one JSON object with matching IDs from `lessons/*.json` and `skills.json`, and run `.venv\Scripts\python.exe -m pytest tests\test_resources.py -q` before running the full suite. Update `verified_at` only after checking the page again. Do not label an entire learning path free when only some rooms or machines are free.

The initial pages were checked on 2026-10-21: [TryHackMe free rooms](https://tryhackme.com/free-rooms) and [Network Fundamentals](https://tryhackme.com/module/network-fundamentals), [Hack The Box Starting Point](https://help.hackthebox.com/en/articles/6007919-introduction-to-starting-point), [OverTheWire Bandit](https://overthewire.org/wargames/bandit/), [PortSwigger Web Security Academy](https://portswigger.net/web-security), and [Professor Messer Network+](https://www.professormesser.com/network-plus/n10-009/n10-009-video/n10-009-training-course/). TryHackMe's general module and HTB's mixed-access Starting Point are marked **Unknown**, not universally free. Individual TryHackMe rooms in the catalog appear on its official free-room list. Durations are shown only when stated on an official page; numbers are rounded up to whole minutes in Telegram.

On an Academy lesson summary or completed quiz result, tap **📚 Дополнительные материалы**. The bot shows up to three resources with a reason, provider, level, access status, and duration when known. After all three lessons of a module are read, it includes a larger path or challenge series when one matches. Category controls show **📹 Видео**, **💻 Практика**, and **📖 Материалы**. The user can select a preferred difficulty, request a resource link, mark it **✅ Я прошёл**, and return through **◀️ Назад** or **🏠 Главное меню**. A missing category shows an empty state. Recommendations are deterministic: completed resources are omitted, repeated and previously opened suggestions rank lower, verified free resources rank higher, assessed weak skills favor reinforcement, and fully assessed mastered skills favor practice. Unassessed skills are never labeled weak. Explicit prerequisite lesson IDs hold back advanced resources until those lessons are read. This checkout has no practical-challenge performance records, so that signal cannot yet affect ranking.

`resource_activity` and `resource_preferences` are additive SQLite tables in `NEWS_DB_PATH`. They keep user-specific recommendation timestamps/counts, link-request timestamps, self-reported completion timestamps, optional notes (up to 500 characters through the storage API), and difficulty preference. Telegram URL buttons do not report external clicks, so **opened** means the learner requested the link from CyberPath; the bot cannot verify a visit. **Я прошёл** is a self-report, does not prove completion on another platform, and never changes Academy mastery. The current Telegram UI does not edit notes. No external credentials are requested; use only authorized training labs. Old resource buttons expire after two hours or when a newer resource view replaces them. AI keys may remain unset; this feature makes no AI requests.

Windows CMD verification from the project folder:

```cmd
cd /d "C:\Users\User\Desktop\tg bot"
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
.venv\Scripts\python.exe -m pytest -q
set "TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN"
set "ALLOWED_USER_IDS=YOUR_NUMERIC_TELEGRAM_ID"
set "OPENROUTER_API_KEY="
set "OPENAI_API_KEY="
.venv\Scripts\python.exe -m cyberpath_ai
```

In a private bot chat: `/learn` → Networking Fundamentals → IP Addressing and Subnets → advance to **Итог урока** → **📚 Дополнительные материалы**. Open one recommendation, inspect its original-source URL, mark it completed, and use Back/Home. Reopen the materials to confirm the self-reported item is omitted. Complete all three Networking lessons to see a larger path suggestion. Restart the bot and reopen the same lesson to confirm activity persists without erasing quiz or lesson progress. If `.venv` is already installed, skip its creation and installation commands.

## Remaining work

Answer appeals and difficult AI-assisted educational evaluations remain future work. The current Academy grades only curated, explicitly accepted answers; a correct synonym absent from JSON must be added to the answer key. Progress is stored locally in SQLite, so moving machines requires moving the database file. Review schedules are created only for finalized answered questions, and users open the review queue manually; there are no scheduled review notifications.

The `.env` file, virtual environment, and SQLite files are covered by `.gitignore`.
