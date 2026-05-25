# EmailAuto

[![CI](https://github.com/nightjaz/emailauto/actions/workflows/ci.yml/badge.svg)](https://github.com/nightjaz/emailauto/actions/workflows/ci.yml)

EmailAuto is a local assistant pipeline for:

- reviewing unread and actionable Gmail messages
- preparing Gmail reply drafts for your approval
- remembering preferences by person and project in a local vault
- producing short morning, midday, and evening briefs with important mail, deadlines, people waiting on you, and project follow-ups

It never sends email automatically and does not archive, delete, mark read, label, or otherwise modify messages. The only Gmail write it performs is creating draft replies for review.

## Quick Start

1. Create a Google OAuth desktop credential in Google Cloud Console.
2. Download it as `credentials.json` into this folder.
3. Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

4. Copy `.env.example` to `.env` and fill in values. No OpenAI API key is required if you want the rule-based version.
5. Initialize the local vault:

```powershell
.\.venv\Scripts\python -m emailauto init
```

6. Authorize Google access:

```powershell
.\.venv\Scripts\python -u -m emailauto auth
```

Open the printed URL, approve access, and wait for `token.json` to be saved.

7. Run the default workflow:

```powershell
.\.venv\Scripts\python -m emailauto run --label morning-brief
```

This creates the brief, prepares Gmail drafts once per email for messages that need replies, keeps local audit copies in `drafts/`, and updates the vault. It never sends email.

8. Generate a read-only brief without creating drafts:

```powershell
.\.venv\Scripts\python -m emailauto brief --label midday-brief
```

9. Preview draft text without creating Gmail drafts:

```powershell
.\.venv\Scripts\python -m emailauto scan --dry-run --limit 5
```

## Remembering Preferences

Add a preference for a person:

```powershell
.\.venv\Scripts\python -m emailauto remember person "Asha Rao" "Prefers short bullets and direct timelines."
```

Add a preference for a project:

```powershell
.\.venv\Scripts\python -m emailauto remember project "Website Revamp" "Keep stakeholders copied on launch-risk decisions."
```

Project context, daily briefs, TODOs, and draft ledgers are stored in `vault/`. Structured interaction memory is also stored locally at `data/vault.sqlite3`.

## Gmail Query

The default pending-email query is:

```text
is:unread
```

Override it in `.env`:

```text
EMAILAUTO_GMAIL_QUERY=is:unread
```

Reply drafts are only created for recent messages by default. Override the draft age window in `.env`:

```text
EMAILAUTO_MAX_DRAFT_AGE_DAYS=3
```

Test runs are capped at 10 external API calls by default, counting both Gmail and OpenAI calls. Override only with approval:

```text
EMAILAUTO_MAX_API_CALLS=10
```

Briefs use an age/importance balance instead of listing every unread email. Fresh FYI mail can appear, older mail needs stronger signals such as a direct ask, deadline, meeting, project, proposal, or urgent language, and very old mail appears only when it is strongly actionable.

## Scheduled Brief Automation

On Windows, run these once from PowerShell after setup:

```powershell
.\scripts\install_morning_brief_task.ps1 -Time "08:00"
.\scripts\install_midday_brief_task.ps1 -Time "13:00"
.\scripts\install_evening_debrief_task.ps1 -Time "17:30"
```

Each task runs `python -m emailauto run`, writes a short brief into `vault/daily/`, updates TODO/project context, and creates Gmail drafts only for messages that do not already have a recorded draft.

## Notes

- This version does not require an OpenAI API key.
- Drafts are formal, professional, and conservative unless an AI provider is added.
- Calendar is intentionally not used.
