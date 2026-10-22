# Educational content and third-party attribution audit

Audit date: 2026-10-22. This is a repository-content audit, not proof of
authorship or permission from external publishers. No lesson or resource JSON
was changed. Stage 4.6 added a separate fictional investigation JSON file.

The repository owner confirmed that rights to the existing original Russian
lesson, quiz, skill-title, and resource-description text are **not yet
verified**. The active content-license scope therefore covers no current JSON
works; see `CONTENT_LICENSE_SCOPE.md`.

## Files reviewed

| Files | Inventory | Findings |
| --- | --- | --- |
| `cyberpath_ai/academy/lessons/networking.json`, `linux.json`, `web_security.json` | Three modules, nine lessons, 45 quiz questions. | Russian explanations, examples, answer feedback, and hints are stored in the repository. The JSON has no author, license, or source-attribution fields. Networking mentions RFC 1918, but a reference to a standard does not establish authorship of the lesson wording. |
| `cyberpath_ai/academy/skills.json` | 18 skill titles and mappings. | No author or license metadata. Some labels are general technical terms. |
| `cyberpath_ai/academy/resources.json` | 16 entries from seven external providers. | Entries contain provider names, page titles, exact external HTTPS URLs, short Russian descriptions and recommendation rationales. They do not bundle the linked videos, labs, pages, or screenshots. The longest `description_ru` is 140 characters; its authorship still needs confirmation. |
| `cyberpath_ai/academy/investigations.json` | Three new fictional scenarios, five steps each. | The scenarios use synthetic Linux listings, TEST-NET IP ranges, and a fictional company. They reference existing resource IDs but do not copy external lab exercises or fetched pages. Rights and license scope for this new educational text still need an explicit owner decision; no CC BY grant is currently made. |
| Runtime news from CISA KEV, NVD, and BleepingComputer | Not bundled in the repository. | Feed titles, descriptions, and articles remain third-party material. Source links and dates should stay visible in the bot. |

The external providers named in the resource catalog are Hack The Box, MDN Web
Docs, OWASP, OverTheWire, PortSwigger Web Security Academy, Professor Messer,
and TryHackMe. All catalog links use HTTPS and point to domains associated
with the named providers. Their names, course titles, pages, and media are not
CyberPath property. The catalog's URLs identify the original sources; an
external link does not grant permission to copy that site's content.

An automated inspection found no embedded external URLs in the lesson JSON,
no bundled third-party media, and no copyright or license notice inside the
educational JSON. It cannot establish whether the Russian wording was written
by the repository owner, adapted with permission, or contributed by others.
The single repository commit also does not establish those rights.

## Before applying CC BY 4.0

The owner should identify which lesson and catalog passages they authored or
have permission to license, including any AI-assisted or contributor-written
text. Only those passages should appear in the active content-license scope.
If a passage was adapted from another source, record that source, its license,
the changes made, and the permission that allows redistribution. If rights are
unclear, leave the passage outside the CC BY grant until resolved or replace it
with verifiably original writing.

Keep external provider names and links as references; do not label their pages,
news articles, labs, videos, or course titles as CyberPath-owned content. Ask
contributors to identify authorship and primary factual references in pull
requests, as `CONTRIBUTING.md` and the pull request template require.
