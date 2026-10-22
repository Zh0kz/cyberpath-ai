# Licensing and third-party material

The repository owner approved **MIT for original software code**. The
copyright line in the root [`LICENSE`](LICENSE) uses the approved name,
**CyberPath AI contributors**. This grant covers original Python application
and test code, project configuration, the CI workflow, and original software
documentation. Contributors retain their copyrights and should submit code
only if they can license their contribution under MIT.

The MIT grant does **not** claim rights to external material or to the
educational JSON files listed below. A root license badge is not a blanket
license for every item reachable through this repository.

## Free educational content: rights still being verified

The owner chose **CC BY 4.0** for original free educational text **once the
right to license each item has been verified**. The owner has not yet confirmed
rights to the existing lesson, quiz, skill-title, or resource-description text.
Therefore **none of the existing educational JSON is currently offered under
CC BY 4.0 by this repository**. See [`CONTENT_LICENSE_SCOPE.md`](CONTENT_LICENSE_SCOPE.md)
for the exact pending paths and the process for adding verified works.
The new fictional investigation scenarios are also excluded from any CC BY
grant until the rights holder makes an explicit licensing decision.

This is a scope decision, not an assertion that the existing content belongs
to a third party. It avoids granting rights the project has not verified.
Read the [content audit](docs/CONTENT_AUDIT.md) for findings and open questions.

CC BY 4.0 allows sharing and adaptation, including commercial use, with
attribution and an indication of changes. Once a verified work is released
under CC BY 4.0, recipients may continue to use that release under its terms.
Future separately authored premium work can still have different terms; an
already licensed free lesson cannot later be made exclusive again.

## Third-party exclusions

The project does not own or relicense CISA KEV, NVD, BleepingComputer news,
external articles, RFCs, OWASP pages, or linked courses, labs, videos, and
media. The resource catalog contains provider names, page titles, URLs, and
metadata identifying those sources; their owners retain their rights. The
proposed CC BY scope would cover only separately verified original CyberPath
descriptions and teaching text, not the linked material itself. Follow each
source's terms when using its content.

Any future premium courses must be authored and licensed separately. They are
outside both the MIT code grant and any future CC BY free-content grant unless
their rights holder expressly says otherwise.

## Direct dependency review

The direct Python dependencies in `pyproject.toml` are installed separately;
their code is not copied into this repository. The locally installed versions
reviewed on 2026-10-22 were:

| Dependency | Role | Upstream license |
| --- | --- | --- |
| [aiogram](https://github.com/aiogram/aiogram/blob/dev-3.x/LICENSE) 3.31.0 | Telegram framework | MIT |
| [openai-python](https://github.com/openai/openai-python/blob/main/LICENSE) 2.54.0 | Optional AI SDK | Apache-2.0 |
| [tzdata](https://github.com/python/tzdata/blob/master/LICENSE) 2026.5 | IANA timezones on Windows | Apache-2.0 |
| [pytest](https://github.com/pytest-dev/pytest/blob/main/LICENSE) 9.1.1 | Development tests | MIT |

These projects retain their own copyrights and license notices. Recheck
resolved versions and any transitive dependency obligations before a packaged
release.
