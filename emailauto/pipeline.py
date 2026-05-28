from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime, parseaddr
import re
from typing import TYPE_CHECKING

from .api_budget import ApiCallBudget, ApiCallBudgetExceeded
from .config import Settings
from .llm import DraftComposer
from .vault import Vault, VaultMemory

if TYPE_CHECKING:
    from .google_services import EmailThread as GmailThread
    from .outlook_services import EmailThread as OutlookThread
    EmailThread = GmailThread | OutlookThread


def _get_email_service(settings: Settings, api_budget: ApiCallBudget):
    """Factory function to get email service based on provider config."""
    if settings.email_provider == "outlook":
        from .outlook_services import OutlookServices
        if not settings.outlook_client_id:
            raise ValueError("OUTLOOK_CLIENT_ID not configured")
        return OutlookServices(
            settings.outlook_client_id,
            settings.outlook_tenant_id,
            settings.outlook_token_path,
            api_budget,
            settings.outlook_refresh_token,
        )
    else:
        from .google_services import GoogleServices
        return GoogleServices(settings.credentials_path, settings.token_path, api_budget)


def _get_email_query(settings: Settings) -> str:
    """Get the appropriate email query based on provider."""
    if settings.email_provider == "outlook":
        return settings.outlook_query
    return settings.gmail_query


def _get_llm_api_key(settings: Settings) -> str | None:
    """Get the appropriate LLM API key (prefer Gemini)."""
    return settings.gemini_api_key or settings.openai_api_key


def _get_llm_provider(settings: Settings) -> str:
    """Determine LLM provider from settings."""
    if settings.gemini_api_key:
        return "gemini"
    return "openai"


def run_scan(settings: Settings, limit: int, dry_run: bool, create_drafts: bool) -> list[str]:
    vault = Vault(settings.vault_path)
    vault.init()
    api_budget = ApiCallBudget(settings.max_api_calls)
    llm_key = _get_llm_api_key(settings)
    reserve_calls = 2 if create_drafts else 1 if llm_key else 0
    email_service = _get_email_service(settings, api_budget)
    composer = DraftComposer(
        llm_key,
        settings.model,
        settings.default_tone,
        settings.user_name,
        api_budget,
        _get_llm_provider(settings),
    )
    logs: list[str] = []
    email_query = _get_email_query(settings)
    for thread_id in email_service.list_threads(email_query, _thread_fetch_limit(limit, api_budget, reserve_calls)):
        if not api_budget.can_spend():
            logs.append(_api_budget_log(api_budget))
            break
        thread = email_service.get_thread(thread_id)
        if not _should_create_draft(settings, thread):
            logs.append(f"[skip-no-draft] {thread.sender} | {thread.subject}")
            continue
        memories = _memories_for_thread(settings, vault, thread)
        try:
            draft = composer.compose(thread, memories)
        except ApiCallBudgetExceeded as exc:
            logs.append(f"[api-budget-reached] {exc}")
            break
        vault.add_interaction(
            message_id=thread.message_id,
            sender=thread.sender,
            subject=thread.subject,
            summary=draft.summary,
            project=draft.project,
        )
        if dry_run or not create_drafts:
            logs.append(f"[preview] {thread.sender} | {thread.subject} | {draft.priority}")
            logs.append(draft.body)
            continue
        if _draft_already_created(settings, thread.message_id):
            logs.append(f"[skip-existing-draft] {thread.sender} | {thread.subject}")
            continue
        try:
            draft_id = email_service.create_draft_reply(thread, draft.body, settings.user_signature)
        except ApiCallBudgetExceeded as exc:
            logs.append(f"[api-budget-reached] {exc}")
            break
        _mark_draft_created(settings, thread.message_id)
        draft_path = _save_local_draft(settings, thread, draft.body, draft.priority)
        logs.append(
            f"[draft:{draft_id}] [audit:{draft_path}] "
            f"{thread.sender} | {thread.subject} | {draft.priority}"
        )
    return logs


def run_brief(settings: Settings, email_limit: int = 20, label: str = "morning-brief") -> str:
    api_budget = ApiCallBudget(settings.max_api_calls)
    threads = _fetch_pending_threads(settings, email_limit, api_budget)
    _update_markdown_vault(settings, threads)
    path = _write_brief(settings, threads, label, [])
    return str(path)


def run_workflow(settings: Settings, email_limit: int = 20, label: str = "morning-brief") -> tuple[str, list[str]]:
    api_budget = ApiCallBudget(settings.max_api_calls)
    llm_key = _get_llm_api_key(settings)
    draft_reserve = 2 if llm_key else 1
    threads = _fetch_pending_threads(settings, email_limit, api_budget, reserve_calls=draft_reserve)
    _update_markdown_vault(settings, threads)
    draft_logs = _create_needed_drafts(settings, threads, api_budget)
    path = _write_brief(settings, threads, label, draft_logs)
    return str(path), draft_logs


def _fetch_pending_threads(
    settings: Settings,
    email_limit: int,
    api_budget: ApiCallBudget,
    reserve_calls: int = 0,
) -> list:
    email_service = _get_email_service(settings, api_budget)
    email_query = _get_email_query(settings)
    thread_ids = email_service.list_threads(email_query, _thread_fetch_limit(email_limit, api_budget, reserve_calls))
    threads = []
    for thread_id in thread_ids:
        if not api_budget.can_spend(1 + reserve_calls):
            break
        threads.append(email_service.get_thread(thread_id))
    return threads


def _create_needed_drafts(
    settings: Settings,
    threads: list,
    api_budget: ApiCallBudget,
) -> list[str]:
    vault = Vault(settings.vault_path)
    vault.init()
    email_service = _get_email_service(settings, api_budget)
    llm_key = _get_llm_api_key(settings)
    composer = DraftComposer(
        llm_key,
        settings.model,
        settings.default_tone,
        settings.user_name,
        api_budget,
        _get_llm_provider(settings),
    )
    logs: list[str] = []
    for thread in threads:
        if not _should_create_draft(settings, thread):
            continue
        if _draft_already_created(settings, thread.message_id):
            logs.append(f"Skipped existing draft: {thread.sender} | {thread.subject}")
            continue
        memories = _memories_for_thread(settings, vault, thread)
        try:
            draft = composer.compose(thread, memories)

            # Skip if AI determined no reply is needed
            if not draft.body:
                logs.append(f"No reply needed: {thread.sender} | {thread.subject} ({draft.summary})")
                _mark_draft_created(settings, thread.message_id)
                continue

            draft_id = email_service.create_draft_reply(thread, draft.body, settings.user_signature)
        except ApiCallBudgetExceeded as exc:
            logs.append(f"Stopped at API budget: {exc}")
            break
        _mark_draft_created(settings, thread.message_id)
        _save_local_draft(settings, thread, draft.body, draft.priority, draft_id)
        vault.add_interaction(
            message_id=thread.message_id,
            sender=thread.sender,
            subject=thread.subject,
            summary=draft.summary,
            project=draft.project or _detect_project(thread),
        )
        logs.append(f"Created draft: {thread.sender} | {thread.subject}")
    return logs


def _thread_fetch_limit(email_limit: int, api_budget: ApiCallBudget, reserve_calls: int = 0) -> int:
    available_after_list = max(api_budget.remaining - 1 - reserve_calls, 0)
    return min(max(email_limit, 0), available_after_list)


def _api_budget_log(api_budget: ApiCallBudget) -> str:
    return f"[api-budget-reached] Used {api_budget.used_calls}/{api_budget.max_calls} API calls."


def _people_from_thread(thread: EmailThread) -> list[str]:
    display_name, email_address = parseaddr(thread.sender)
    people = [thread.sender, display_name, email_address]
    if display_name:
        people.append(_safe_filename(display_name).replace("-", " "))
    return [person for person in people if person]


def _projects_from_thread(thread: EmailThread) -> list[str]:
    cleaned = re.sub(r"(?i)^(re|fwd):\s*", "", thread.subject).strip()
    projects = [cleaned]
    detected = _detect_project(thread)
    if detected:
        projects.append(detected)
    return [project for project in dict.fromkeys(projects) if project]


def _memories_for_thread(settings: Settings, vault: Vault, thread: EmailThread) -> list[VaultMemory]:
    memories = vault.memories_for(_people_from_thread(thread), _projects_from_thread(thread))
    seen = {(memory.kind, memory.key, memory.note) for memory in memories}
    for memory in _markdown_memories_for_thread(settings, thread):
        key = (memory.kind, memory.key, memory.note)
        if key not in seen:
            memories.append(memory)
            seen.add(key)
    return memories


def _markdown_memories_for_thread(settings: Settings, thread: EmailThread) -> list[VaultMemory]:
    updated_at = datetime.now().isoformat()
    memories: list[VaultMemory] = []
    display_name, email_address = parseaddr(thread.sender)

    people_keys = [thread.sender, display_name, email_address]
    for key in people_keys:
        if not key:
            continue
        path = settings.vault_dir / "people" / f"{_safe_filename(key)}.md"
        note = _read_context_note(path)
        if note:
            memories.append(VaultMemory("person", key.strip().lower(), note, updated_at))

    for project in _projects_from_thread(thread):
        path = settings.vault_dir / "projects" / f"{_safe_filename(project)}.md"
        note = _read_context_note(path)
        if note:
            memories.append(VaultMemory("project", project.strip().lower(), note, updated_at))

    todo_note = _read_context_note(settings.vault_dir / "TODO.md", max_chars=1200)
    if todo_note:
        memories.append(VaultMemory("global", "todo", todo_note, updated_at))
    return memories


def _read_context_note(path, max_chars: int = 1800) -> str:
    if not path.exists() or path.name == ".gitkeep":
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:].lstrip()


def _save_local_draft(
    settings: Settings,
    thread: EmailThread,
    body: str,
    priority: str,
    gmail_draft_id: str | None = None,
) -> str:
    settings.drafts_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{date.today().isoformat()}-{thread.message_id}-{_safe_filename(thread.subject)}.md"
    path = settings.drafts_dir / filename
    content = "\n".join(
        [
            "---",
            f"thread_id: {thread.thread_id}",
            f"message_id: {thread.message_id}",
            f"gmail_draft_id: {gmail_draft_id or 'not created'}",
            f"to: {thread.sender}",
            f"subject: Re: {thread.subject}",
            f"priority: {priority}",
            "---",
            "",
            body.rstrip(),
            "",
        ]
    )
    path.write_text(content, encoding="utf-8")
    return str(path)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    return cleaned.strip("-")[:80] or "email-draft"


def _brief_sender_name(thread: EmailThread) -> str:
    forwarded_sender = _forwarded_sender_name(thread)
    if forwarded_sender:
        return forwarded_sender
    display_name, email_address = parseaddr(thread.sender)
    if display_name:
        return display_name.strip('"')
    if email_address:
        return email_address.split("@", 1)[0]
    return thread.sender


def _forwarded_sender_name(thread: EmailThread) -> str | None:
    if not thread.subject.lower().startswith("fwd:"):
        return None
    match = re.search(r"(?im)^from:\s*(.+)$", thread.body)
    if not match:
        return None
    display_name, email_address = parseaddr(match.group(1).strip())
    if display_name:
        return display_name.strip('"')
    if email_address:
        return email_address.split("@", 1)[0]
    return match.group(1).strip() or None


def _brief_subject(thread: EmailThread) -> str:
    subject = re.sub(r"(?i)^(re|fwd):\s*", "", thread.subject).strip()
    return subject or "(no subject)"


def _brief_points(thread: EmailThread, max_points: int = 6) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    for index, unit in enumerate(_content_units(thread)):
        cleaned = _clean_brief_text(unit)
        if not cleaned:
            continue
        if _is_boilerplate_brief_line(cleaned):
            continue
        score = _brief_unit_score(cleaned)
        if score > 0:
            scored.append((score, -index, cleaned))

    points: list[str] = []
    seen: set[str] = set()
    for _, __, point in sorted(scored, reverse=True):
        if _is_repeated_brief_point(point, seen):
            continue
        points.append(point)
        if len(points) == max_points:
            return points
    if points:
        return points

    for unit in _content_units(thread):
        cleaned = _clean_brief_text(unit)
        if cleaned and not _is_boilerplate_brief_line(cleaned) and not _is_repeated_brief_point(cleaned, seen):
            points.append(cleaned)
        if len(points) == min(max_points, 2):
            break
    return points or ["No concise content extracted."]


def _content_units(thread: EmailThread) -> list[str]:
    text = thread.body or thread.snippet
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"(?is)\n[-_=]{5,}.*", "\n", text)
    text = re.sub(r"https?://\S+", " ", text)
    units: list[str] = []
    for raw_line in text.splitlines():
        raw_line = re.sub(r"\[image:[^\]]+\]", " ", raw_line, flags=re.IGNORECASE)
        line = raw_line.strip(" \t-*•")
        if not line:
            continue
        if len(line) <= 220:
            units.append(line)
            continue
        units.extend(
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", line)
            if sentence.strip()
        )
    return units


def _clean_brief_text(value: str) -> str:
    cleaned = re.sub(r"[\u034f\u200c\u200d]+", "", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"(?i)^important:\s*", "", cleaned)
    cleaned = re.sub(r"(?i)^please find (information|details)?\s*(about|regarding)?\s*", "", cleaned).strip()
    return cleaned[:260].rstrip(" ,;-")


def _is_repeated_brief_point(point: str, seen: set[str]) -> bool:
    key = re.sub(r"[^a-z0-9]+", " ", point.lower()).strip()
    if not key:
        return True
    for existing in seen:
        if key in existing or existing in key:
            return True
    seen.add(key)
    return False


def _is_boilerplate_brief_line(value: str) -> bool:
    text = value.lower().strip().replace("'", "'").replace(""", '"').replace(""", '"')
    if re.match(r"^(to|from|date|subject|cc|bcc):\s", text):
        return True
    if text.startswith("hi @") or text.startswith("github, inc."):
        return True
    if re.match(r"^(dear|hi|hello|hey)\s+\w+", text):
        return True
    if re.match(r"^(sir|madam|sir/madam|ma'am)", text):
        return True
    if re.match(r"^(good\s+)?(morning|afternoon|evening|day)", text):
        return True
    if len(text) < 15 and any(word in text for word in ["greetings", "regards", "thanks", "cheers"]):
        return True
    boilerplate = [
        "dear students",
        "dear all",
        "dear faculty",
        "dear team",
        "good morning",
        "greetings",
        "please find",
        "for your information",
        "forwarded message",
        "regards",
        "best regards",
        "warm regards",
        "kind regards",
        "thank you",
        "thanks and regards",
        "to view this email",
        "this communication is intended",
        "you can view",
        "read more about",
        "you're receiving this email",
        "you are receiving this email",
        "you're receiving this",
        "you received this",
        "you have been made",
        "click here",
        "unsubscribe",
        "sent from my",
        "get outlook",
    ]
    return any(text == marker or text.startswith(f"{marker} ") or text.startswith(f"{marker},") for marker in boilerplate)


def _brief_unit_score(value: str) -> int:
    text = value.lower()
    score = 0

    if len(text) < 20:
        score -= 3
    elif len(text) > 50:
        score += 1

    if re.search(r"\b\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", text):
        score += 4
    if re.search(r"\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b", text):
        score += 3
    for marker in [
        "deadline",
        "last date",
        "register",
        "registration",
        "submission",
        "evaluation",
        "apply by",
        "form responses",
        "schedule",
        "meeting",
        "session",
        "today",
        "tomorrow",
        "required",
        "must",
        "action",
        "respond",
        "confirm",
        "submit",
        "complete",
    ]:
        if marker in text:
            score += 3
    for marker in [
        "eligib",
        "team",
        "participants",
        "expertise",
        "robotics",
        "drones",
        "agriculture",
        "sensors",
        "google form",
        "uploaded",
        "campus",
        "prize",
        "winner",
        "award",
        "opportunity",
        "internship",
        "placement",
        "interview",
    ]:
        if marker in text:
            score += 2
    if re.search(r"\bai\b", text):
        score += 2
    for marker in ["organized by", "organised by", "imperial college", "bits pilani"]:
        if marker in text:
            score += 4
    if "?" in re.sub(r"https?://\S+", "", value):
        score += 2
    if re.search(r"(rs\.?|inr|₹|\$)\s*[\d,]+", text):
        score += 3
    return score


def _write_brief(settings: Settings, threads: list, label: str, draft_logs: list[str]) -> str:
    settings.briefs_dir.mkdir(parents=True, exist_ok=True)
    grouped = {
        "Urgent Deadlines": [],
        "Waiting On Me": [],
        "Important": [],
        "FYI": [],
    }
    for thread in threads:
        if not _should_include_in_brief(thread):
            continue
        if _is_low_value(thread):
            grouped["FYI"].append(thread)
        elif _has_deadline(thread):
            grouped["Urgent Deadlines"].append(thread)
        elif _needs_response(thread):
            grouped["Waiting On Me"].append(thread)
        elif _is_important(thread):
            grouped["Important"].append(thread)
        else:
            grouped["FYI"].append(thread)

    title = label.replace("-", " ").title()
    lines = [f"# {title} - {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]

    # Generate smart summaries using Gemini
    summaries = {}
    if settings.gemini_api_key and threads:
        try:
            from .clustering import EmailSummarizer
            summarizer = EmailSummarizer(settings.gemini_api_key, settings.model)
            summaries = summarizer.summarize_emails(threads)
        except Exception:
            pass

    for section in ("Urgent Deadlines", "Waiting On Me", "Important", "FYI"):
        lines.append(f"## {section}")
        if not grouped[section]:
            lines.append("- None.")
        for thread in grouped[section]:
            summary = summaries.get(thread.message_id)
            if summary:
                from .clustering import EmailSummarizer
                lines.extend(EmailSummarizer(None, "").format_summary_for_brief(thread, summary))
            else:
                lines.append(f"- **{_brief_sender_name(thread)}**: {_brief_subject(thread)}")
                for point in _brief_points(thread):
                    lines.append(f"  - {point}")
        lines.append("")

    lines.append("## Drafts Created")
    if not draft_logs:
        lines.append("- No new drafts created.")
    else:
        for log in draft_logs:
            lines.append(f"- {log}")
    lines.extend(["", "## Next Actions", "- Review drafts before sending.", "- Check TODO items for deadlines and pending replies.", ""])

    filename = f"{datetime.now().strftime('%Y-%m-%d')}-{_safe_filename(label)}.md"
    path = settings.briefs_dir / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _update_markdown_vault(settings: Settings, threads: list) -> None:
    for folder in ("people", "projects", "daily", "notes"):
        (settings.vault_dir / folder).mkdir(parents=True, exist_ok=True)
    todo_items = {
        "Deadlines": [],
        "Waiting On Me": [],
        "Important": [],
    }
    for thread in threads:
        _update_person_context(settings, thread)
        _update_project_context(settings, thread)
        if not _should_include_in_brief(thread):
            continue
        if _is_low_value(thread):
            continue
        if _has_deadline(thread):
            if _needs_response(thread):
                todo_items["Deadlines"].append(f"Respond to {thread.sender} about {thread.subject}")
            else:
                todo_items["Deadlines"].append(f"Review deadline from {thread.sender} about {thread.subject}")
        elif _needs_response(thread):
            todo_items["Waiting On Me"].append(f"Respond to {thread.sender} about {thread.subject}")
        elif _is_important(thread):
            todo_items["Important"].append(f"Review {thread.sender} about {thread.subject}")
    _update_todo(settings, todo_items)


def _update_project_context(settings: Settings, thread: EmailThread) -> None:
    project = _detect_project(thread)
    if not project:
        return
    path = settings.vault_dir / "projects" / f"{_safe_filename(project)}.md"
    entry = (
        f"\n## {date.today().isoformat()}\n"
        f"- Message ID: {thread.message_id}\n"
        f"- Source: {thread.sender}\n"
        f"- Subject: {thread.subject}\n"
        f"- Latest status: {thread.snippet or 'Mentioned in email.'}\n"
    )
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if thread.message_id not in existing:
            path.write_text(existing.rstrip() + f"\n{entry}", encoding="utf-8")
        return
    path.write_text(f"# {project}\n{entry}", encoding="utf-8")


def _update_person_context(settings: Settings, thread: EmailThread) -> None:
    display_name, email_address = parseaddr(thread.sender)
    key = display_name or email_address or thread.sender
    if not key:
        return
    path = settings.vault_dir / "people" / f"{_safe_filename(key)}.md"
    entry = (
        f"\n## {date.today().isoformat()}\n"
        f"- Email: {email_address or thread.sender}\n"
        f"- Message ID: {thread.message_id}\n"
        f"- Latest subject: {thread.subject}\n"
        f"- Latest status: {thread.snippet or 'Mentioned in email.'}\n"
    )
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if thread.message_id not in existing:
            path.write_text(existing.rstrip() + f"\n{entry}", encoding="utf-8")
        return
    path.write_text(f"# {key}\n{entry}", encoding="utf-8")


def _update_todo(settings: Settings, items: dict[str, list[str]]) -> None:
    path = settings.vault_dir / "TODO.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Open Loops and Action Items\n"
    lines = [existing.rstrip(), "", f"## Processed {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    for section in ("Deadlines", "Waiting On Me", "Important"):
        lines.append(f"\n### {section}")
        new_items = [item for item in items[section] if item not in existing]
        if not new_items:
            lines.append("- [ ] No new items.")
        for item in new_items:
            lines.append(f"- [ ] {item}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _read_draft_ledger(settings: Settings) -> set[str]:
    path = settings.vault_dir / "notes" / "gmail_drafted_message_ids.txt"
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _draft_already_created(settings: Settings, message_id: str) -> bool:
    return message_id in _read_draft_ledger(settings)


def _mark_draft_created(settings: Settings, message_id: str) -> None:
    path = settings.vault_dir / "notes" / "gmail_drafted_message_ids.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_draft_ledger(settings)
    if message_id in existing:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{message_id}\n")


def _thread_text(thread: EmailThread) -> str:
    return f"{thread.subject} {thread.snippet} {thread.body}".lower()


def _should_create_draft(settings: Settings, thread: EmailThread) -> bool:
    if _is_low_value(thread):
        return False
    if _is_stale_for_draft(settings, thread):
        return False
    return _needs_response(thread)


def _should_include_in_brief(thread: EmailThread) -> bool:
    age_days = _message_age_days(thread)
    score = _importance_score(thread)
    if age_days is None:
        return score >= 2
    if age_days <= 2:
        return score >= -1
    if age_days <= 7:
        return score >= 3
    if age_days <= 14:
        return score >= 5
    return score >= 7


def _is_stale_for_draft(settings: Settings, thread: EmailThread) -> bool:
    if settings.max_draft_age_days <= 0:
        return False
    message_date = _parsed_message_date(thread.date)
    if not message_date:
        return False
    age = datetime.now(timezone.utc) - message_date.astimezone(timezone.utc)
    return age > timedelta(days=settings.max_draft_age_days)


def _message_age_days(thread: EmailThread) -> float | None:
    message_date = _parsed_message_date(thread.date)
    if not message_date:
        return None
    age = datetime.now(timezone.utc) - message_date.astimezone(timezone.utc)
    return max(age.total_seconds() / 86400, 0)


def _parsed_message_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_low_value(thread: EmailThread) -> bool:
    text = _thread_text(thread)
    return any(
        marker in text
        for marker in [
            "unsubscribe",
            "newsletter",
            "50% off",
            "premium",
            "sale",
            "digest",
            "no action required",
        ]
    )


def _importance_score(thread: EmailThread) -> int:
    if _is_low_value(thread):
        return -4

    text = _thread_text(thread)
    sender = thread.sender.lower()
    score = 0

    if _needs_response(thread):
        score += 5
    if _has_deadline(thread):
        score += 4
    if any(marker in text for marker in ["urgent", "asap", "blocked", "waiting on you"]):
        score += 3
    if any(
        marker in text or marker in sender
        for marker in [
            "professor",
            "faculty",
            "registrar",
            "placement",
            "dues",
            "project",
            "application",
            "proposal",
            "meeting",
            "task",
            "follow up",
        ]
    ):
        score += 2
    if any(
        marker in text
        for marker in [
            "internship",
            "hackathon",
            "webinar",
            "colloquia",
            "invitation",
            "announcement",
            "for your information",
        ]
    ):
        score += 1
    if _looks_like_broadcast(thread):
        score -= 1

    return score


def _has_deadline(thread: EmailThread) -> bool:
    text = _thread_text(thread)
    return any(
        marker in text
        for marker in [
            "deadline",
            "due today",
            "due tomorrow",
            "submit by",
            "last date",
            "before eod",
            "by today",
            "by tomorrow",
        ]
    )


def _needs_response(thread: EmailThread) -> bool:
    if _is_low_value(thread):
        return False
    text = re.sub(r"https?://\S+", "", _thread_text(thread))
    direct_reply_markers = [
        "please respond",
        "please reply",
        "please confirm",
        "kindly confirm",
        "confirm your",
        "can you",
        "could you",
        "would you",
        "let me know",
        "wanted to know",
        "want to know",
        "when you would",
        "when you can",
        "when you could",
        "share your",
        "send me",
        "approval required",
        "your approval",
        "your decision",
        "waiting on you",
    ]
    if "?" in text:
        return True
    if any(marker in text for marker in direct_reply_markers):
        return True
    if _looks_like_broadcast(thread):
        return False
    return any(
        marker in text
        for marker in [
            "please review and respond",
            "please advise",
            "your input",
            "your feedback",
            "action required from you",
        ]
    )


def _looks_like_broadcast(thread: EmailThread) -> bool:
    text = _thread_text(thread)
    sender = thread.sender.lower()
    broadcast_markers = [
        "dear students",
        "dear all",
        "dear faculty colleagues and students",
        "for your information",
        "this is to inform",
        "please find enclosed",
        "please find attached",
        "forwarded message",
        "announcement",
        "events of the day",
        "webinar",
        "internship",
        "hackathon",
        "colloquia",
        "invitation",
        "kindly participate",
        "applications are now open",
    ]
    broadcast_senders = [
        "office",
        "announcement",
        "registrar",
        "swd",
        "placement",
        "library",
        "mathworks",
        "notion",
        "chess.com",
    ]
    return any(marker in text for marker in broadcast_markers) or any(
        marker in sender for marker in broadcast_senders
    )


def _is_important(thread: EmailThread) -> bool:
    if _is_low_value(thread):
        return False
    text = _thread_text(thread)
    sender = thread.sender.lower()
    return _needs_response(thread) or any(
        marker in text or marker in sender
        for marker in [
            "professor",
            "faculty",
            "registrar",
            "placement",
            "deadline",
            "dues",
            "project",
            "internship",
            "application",
            "proposal",
            "meeting",
            "task",
            "follow up",
        ]
    )


def _detect_project(thread: EmailThread) -> str | None:
    text = f"{thread.subject} {thread.snippet}"
    bracket = re.search(r"\[([A-Za-z0-9 _.-]{2,60})\]", text)
    if bracket:
        return bracket.group(1).strip()
    subject = re.sub(r"(?i)^(re|fwd):\s*", "", thread.subject).strip()
    if any(word in subject.lower() for word in ["project", "initiative", "internship", "hackathon", "proposal", "webinar"]):
        return subject[:60]
    project_match = re.search(
        r"(?i)\b(project|initiative|internship|hackathon|webinar|proposal)\b[: -]+([^|,\n]{3,60})",
        text,
    )
    if project_match:
        return project_match.group(2).strip()
    return None
