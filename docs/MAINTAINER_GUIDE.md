# Maintainer setup checklist

These GitHub settings are recommendations. They have **not** been changed by
the files in this repository.

## Discussions

In the repository, open **Settings → General → Features** and enable
**Discussions**. Then open Discussions and create or rename categories:

| Category | Use |
| --- | --- |
| Ideas | Early product or teaching ideas before an issue is scoped. |
| Questions | Help with installation, configuration, or lessons. |
| Learning Content Suggestions | Topics, corrections, and source-backed resource ideas. |
| General Feedback | Learner experience and usability. |
| Announcements | Maintainer updates; restrict creation to maintainers if desired. |

Use an Issue for a reproducible bug or a specific change ready to track.
Discussions are for open-ended conversation. Do not ask people to share bot
tokens or private learner data in either place.

## Labels

Under **Issues → Labels**, keep or create these labels with short descriptions:

| Label | Meaning |
| --- | --- |
| `bug` | Confirmed or suspected incorrect behavior. |
| `enhancement` | A specific product improvement. |
| `documentation` | Instructions or explanations need work. |
| `good first issue` | Small, well-scoped task with guidance. |
| `help wanted` | Maintainer welcomes outside help. |
| `learning-content` | Lesson, quiz, skill, or resource catalog change. |
| `security` | Public tracking of a fix **after** private triage, without exploit details or credentials. |

Some labels may already exist. Avoid opening a public `security` issue to
disclose an unpatched vulnerability.

## Protect `main` and review pull requests

In **Settings → Rules → Rulesets** (or **Branches → Branch protection rules**),
create a rule targeting `main`. Require a pull request and at least one
approving review, the `pytest` GitHub Actions check after it has run once,
and resolution of review conversations. Block force pushes and deletion.
Consider dismissing stale approvals after new commits. Review the diff,
educational references and rights, dependency changes, and CI changes before
merging. Do not approve a pull request merely because tests pass.

Run external pull requests through the existing `pull_request` CI workflow.
That workflow grants only `contents: read`, does not persist checkout
credentials, and passes no repository secrets or real API keys to the test
process. Keep `pull_request_target` and privileged `workflow_run` jobs away
from untrusted contributor code. Do not put production credentials in CI.
Keep GitHub's fork approval policy enabled, especially for first-time
contributors. Review changes to `.github/workflows/`, dependencies, and build
scripts with extra care because CI executes contributor code.

## Private vulnerability reports

In **Settings → Security and code quality → Advanced Security**, enable
**Private vulnerability reporting**. Reporters can then use **Security →
Advisories → Report a vulnerability**. `SECURITY.md` explains the private
route and what to do if the button is not yet visible.

## Content rights before release

The root `LICENSE` grants MIT rights to original software code. Review
`LICENSING.md`, `CONTENT_LICENSE_SCOPE.md`, and the content audit before
describing any educational JSON as CC BY licensed. The owner has not yet
verified rights to the current lesson and catalog text. Add a work to the CC BY
scope only after documenting its author or permission and attribution.
