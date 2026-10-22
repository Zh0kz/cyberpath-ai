# CyberPath AI

CyberPath AI is a personal cybersecurity learning bot for Telegram. It combines
curated Russian lessons, short quizzes, spaced review, and source-backed security
news. Python, aiogram, and SQLite run it locally; optional AI explanations use a
key you configure. The bot does **not** scan external systems.

**Project status:** The current `0.4.6` code includes the Academy, three offline
practical investigations, News, daily missions, adaptive lesson suggestions,
resource recommendations, and an opt-in news digest. The community documentation
and MIT license cover original software code; rights to existing educational
text are still under review. Broader learning paths, managed multiuser deployment,
and premium courses are ideas for the future, not available features. See
[ROADMAP.md](ROADMAP.md).

## What you can do today

| Feature | Current behavior |
| --- | --- |
| News | Browse recent items from CISA KEV, NVD, and BleepingComputer RSS, one at a time, with the original source link. Failed sources can fall back to cached items. |
| Academy | Read curated Networking, Linux, and Web Security lessons in Russian, with useful English technical terms. |
| Practical investigations | Work through five connected evidence steps in each of three fictional cases: Linux permissions, DNS/TCP traffic, and a SOC login investigation. Give short text answers or choose a defensive decision, use three progressive hints, retry steps, resume after restart, and read a final debrief. These attempts do not change lesson quiz mastery. |
| Quizzes and progress | Take five-question lesson quizzes with hints, deterministic grading, partial credit where defined, and saved progress. |
| Adaptive learning | See a suggested next lesson and manually review questions when spaced repetition says they are due. |
| Daily Mission | Open a saved daily set of news, lesson, quiz, and due-review tasks. Completing each task requires actual activity. |
| Resource recommendations | See curated links to external videos, labs, and reading; access terms are shown where known. The bot does not fetch those pages at runtime. |
| Daily news | Opt in through Settings to receive three or five source-backed items at a saved local hour. The bot process must be running. |
| Optional AI explainer | Request a short Russian explanation of a stored news item through a configured OpenRouter or OpenAI account. No AI key is needed for the Academy, news browsing, or digests. |

The inline menu and Telegram command menu offer `/start`, `/news`, `/learn`,
`/quiz`, `/mission`, `/review`, `/progress`, `/settings`, and `/help`.

## Screenshots

Screenshots are coming. Suggested images: the home dashboard, a lesson, an
investigation step and debrief, and a news article with its original-source
button. Use a demo account and
redact user IDs, private chats, tokens, and personal progress before adding any
image to the repository.

## Run your own bot on Windows CMD

You need Python 3.13, Git, a Telegram account, and a bot token from
[BotFather](https://core.telegram.org/bots/features#botfather). Python 3.12 or
newer is supported; GitHub Actions tests 3.13.

1. In Telegram, open the official **@BotFather** account and send `/newbot`.
   Follow its prompts to choose a name and username. Keep the token private.
2. Find your own **numeric Telegram user ID** using a method you trust. A
   username such as `@name` is not a numeric ID. You will allowlist this ID.
3. Open Windows CMD and run:

```cmd
git clone https://github.com/Zh0kz/cyberpath-ai.git
cd cyberpath-ai
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
```

4. In the **same** CMD window, replace the placeholders and launch:

```cmd
set "TELEGRAM_BOT_TOKEN=YOUR_NEW_BOT_TOKEN"
set "ALLOWED_USER_IDS=YOUR_NUMERIC_TELEGRAM_ID"
.venv\Scripts\python.exe -m cyberpath_ai
```

Send `/start` to **your own** bot in a private chat. Stop the process with
`Ctrl+C`. Daily delivery works only while it is running. Your local data goes
to `data\cyberpath.sqlite3` by default; keep that file and its backups private.

To try Stage 4.6, send `/learn` and tap **🛡️ Практические задания**. Open one
of the three cases, inspect the displayed synthetic evidence, and answer each
step. The bot never executes typed commands or captures live traffic. A wrong
answer can be retried; the complete reasoning is shown after a correct answer,
after revealing an answer with three hints, or in the final debrief. Only the
latest finalized run of each step contributes to its investigation score.
Investigation scores are separate from lesson quizzes and skill mastery. The
debrief links to existing curated external learning resources; their pages are
not fetched by the bot.

Cloning this repository **does not grant access to the maintainer's bot
instance**, token, users, or database. You create and operate a separate bot.
The allowlist checks the sender's numeric Telegram ID before handling commands
and callbacks. An unlisted account receives no bot response. For several
trusted users, set `ALLOWED_USER_IDS` to comma-separated numeric IDs.

The app reads environment variables directly; it does **not** automatically
load `.env`. [.env.example](.env.example) is a reference with placeholders.
If you keep a local `.env` reference, do not commit it. Never paste a token in
an issue, screenshot, or log. If a token leaks, rotate it with its provider.

### Daily news and AI configuration

In Settings, daily news starts **disabled**. Enable it explicitly, choose three
or five articles, and choose a two-digit delivery hour. New preferences default
to `Asia/Almaty` and `09:00`; the current Settings screen displays the saved
timezone but does not edit it. The digest uses real source-backed news and no
AI API. `NEWS_LOOKBACK_HOURS` defaults to 72. The full environment variable
list and defaults are in [.env.example](.env.example).

AI explanations are optional. The default provider is OpenRouter with the
`openrouter/free` route; there is no automatic paid-model fallback. To enable
it, set your own key before launching:

```cmd
set "AI_PROVIDER=openrouter"
set "OPENROUTER_API_KEY=YOUR_OWN_KEY"
set "OPENROUTER_MODEL=openrouter/free"
.venv\Scripts\python.exe -m cyberpath_ai.ai.check_model
.venv\Scripts\python.exe -m cyberpath_ai
```

`check_model` may contact the selected provider to check access but does not
generate an explanation. OpenAI is also supported through `AI_PROVIDER=openai`
and `OPENAI_API_KEY`; that service may charge for use. Set only the provider
you intend to use. Request caps, timeouts, output limits, and concurrency are
configurable; `MAX_AI_CALLS_PER_DAY` defaults to 10. AI output is educational
support, so check incident claims against the original linked source.

## Testing and development

Run the complete offline suite from the repository directory:

```cmd
.venv\Scripts\python.exe -m pytest -q
```

Tests remove live bot/AI credentials from their environment, mock Telegram and
external services, use temporary SQLite data, and reject unexpected outbound
connections. They need no API keys or paid calls. The
[GitHub Actions workflow](.github/workflows/tests.yml) runs this suite on
Python 3.13 for pushes and pull requests with read-only repository permission,
without repository secrets. See [CONTRIBUTING.md](CONTRIBUTING.md) for Windows
setup, lesson and resource edits, tests, and pull requests.

The bot itself fetches public news from [CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog),
[NVD](https://nvd.nist.gov/developers/vulnerabilities), and
[BleepingComputer RSS](https://www.bleepingcomputer.com/rss-feeds/). It stores
original URLs and dates, deduplicates entries, and never generates news items.
Those sources and linked educational resources remain third-party material.

## Community and support

- [Bug report](https://github.com/Zh0kz/cyberpath-ai/issues/new/choose): use
  the template, a small reproduction, and redacted logs.
- [Feature request](https://github.com/Zh0kz/cyberpath-ai/issues/new/choose):
  explain the learner need and your proposed experience.
- [Security report](SECURITY.md): disclose vulnerabilities privately; do not
  post credentials or exploit details in a public issue.
- [Code of Conduct](CODE_OF_CONDUCT.md): keep discussion respectful and safe.

GitHub Discussions are recommended for questions and early ideas once the
maintainer enables them. The proposed categories, issue labels, branch
protection, review process, and private reporting setup are in
[docs/MAINTAINER_GUIDE.md](docs/MAINTAINER_GUIDE.md).

## License and content rights

Original software code and software documentation are licensed under
[MIT](LICENSE), with the owner-approved copyright line “CyberPath AI
contributors.” Existing lesson, quiz, skill-title, and resource-description
text has **not yet been verified for relicensing**, so it is **not currently
offered under CC BY 4.0**. CC BY 4.0 is the chosen license for original free
educational works only after their rights are verified and they are named in
[CONTENT_LICENSE_SCOPE.md](CONTENT_LICENSE_SCOPE.md). External articles,
courses, provider names, and linked media remain under their owners' rights.
Future separately authored premium courses would have their own terms. See
[LICENSING.md](LICENSING.md) and the [content audit](docs/CONTENT_AUDIT.md)
before reusing educational content.

The new investigation scenarios have no CC BY grant pending an explicit rights
and licensing decision.

CyberPath AI is for defensive learning on systems you own or are authorized to
use. It does not provide operational instructions for attacking real systems.
