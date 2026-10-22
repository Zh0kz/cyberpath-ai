# Contributing to CyberPath AI

Thanks for helping make cybersecurity education clearer and safer. Beginners
are welcome. A small bug report, wording fix, or question is useful.

## Before you start

- Search existing issues. For a larger change, open an issue first so we can
  agree on scope and avoid duplicate work.
- Read `CODE_OF_CONDUCT.md` and `SECURITY.md`. Report vulnerabilities privately.
- Never post a Telegram token, AI key, private chat, SQLite database, or
  unredacted log in an issue or pull request.
- Original software code is under MIT. Submit code only if you can license your
  contribution under MIT. Existing educational JSON is not yet CC BY licensed;
  read `CONTENT_LICENSE_SCOPE.md`. For new educational text, identify its
  author and confirm the rights needed for CC BY 4.0 before submission.

## Windows CMD setup

Install Python 3.13 and Git, then run:

```cmd
git clone https://github.com/Zh0kz/cyberpath-ai.git
cd cyberpath-ai
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
.venv\Scripts\python.exe -m pytest -q
```

The tests use temporary SQLite files and mocked external services. They need
no Telegram token, AI key, paid API call, or running bot. To run your own bot,
follow the separate setup in `README.md`. Do not use a production database for
tests or attach it to a report.

## Improve lessons

The free lesson catalog is in `cyberpath_ai/academy/lessons/`, with one JSON
file per module. Start from an existing lesson in that folder. A lesson needs a
unique ID, title, at least two objectives, at least three sections, a summary,
and exactly five questions. Each section has a title, explanation, example,
and concepts. Each question needs a prompt, explanation, and three distinct
progressive hints. Supported question types are `choice`, `text`, and `parts`;
the validator in `cyberpath_ai/academy/content.py` checks answer keys and
partial-credit totals. If you add or change question IDs or skill coverage,
update `cyberpath_ai/academy/skills.json` too.

Write in clear Russian for learners, keeping useful English technical terms.
Use defensive, authorized examples. Check factual claims against reliable
primary references and include URLs in the pull request description. Do not
copy third-party lesson text, screenshots, questions, or course material without
documented permission and compatible licensing. State who authored your text.
Run the full test suite after editing JSON.

## Recommend an external resource

Entries live in `cyberpath_ai/academy/resources.json`; validation is in
`cyberpath_ai/academy/resources.py`. Use the exact official HTTPS page, a
unique resource ID, accurate title/provider, a short original Russian
description, matching lesson and skill IDs, category, difficulty, language,
access status, objectives, and a verification date. Include duration only if
the provider states it. Mark access `Unknown` when the free/paid boundary is
unclear. A URL is a recommendation, not permission to copy its content.

Verify the page and access terms yourself; explain the check in the pull
request. The bot does not visit recommendation links at runtime. Run:

```cmd
.venv\Scripts\python.exe -m pytest tests\test_resources.py -q
.venv\Scripts\python.exe -m pytest -q
```

## Submit a pull request

1. Fork the repository on GitHub and create a branch in your fork.
2. Make one focused change. Add or update tests when behavior changes.
3. Run the complete offline test suite and inspect `git status` before pushing.
4. Open a pull request against `main`. Use the pull request template to explain
   the change, tests, source attribution for educational content, and any UI
   screenshots with personal data removed.
5. Respond to review comments. A maintainer decides when the change is ready.

Please keep external services mocked in tests. Do not add live network calls,
paid API calls, active scans, credentials, or new production dependencies
without discussing the change first. Changes to the bot's allowlist or CI
permissions need explicit security review.
