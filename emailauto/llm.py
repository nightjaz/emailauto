from __future__ import annotations

import json
from dataclasses import dataclass

from .api_budget import ApiCallBudget
from .google_services import EmailThread
from .vault import VaultMemory


@dataclass(frozen=True)
class DraftResult:
    body: str
    summary: str
    project: str | None
    priority: str


class DraftComposer:
    def __init__(
        self,
        api_key: str | None,
        model: str,
        tone: str,
        user_name: str = "",
        api_budget: ApiCallBudget | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.tone = tone
        self.user_name = user_name
        self.api_budget = api_budget
        self.client = None
        if api_key:
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key)

    def compose(self, thread: EmailThread, memories: list[VaultMemory]) -> DraftResult:
        if not self.client:
            return self._template_reply(thread, memories)

        memory_text = "\n".join(f"- {m.kind}:{m.key}: {m.note}" for m in memories) or "- none"
        prompt = f"""
You prepare formal, professional email replies for human approval. Never claim something is done unless the email proves it.
If context is missing, write a cautious reply that says the user will check and get back.
Use the known preferences and vault notes to personalize the reply. Refer to concrete dates, forms, links, deadlines, or requested actions from the email when useful.
Do not draft replies for mass announcements as though the user has committed to participate; acknowledge and defer unless a clear response is required.
Write in this tone: {self.tone}.
User name: {self.user_name or "the sender of this assistant"}

Known preferences:
{memory_text}

Email:
From: {thread.sender}
To: {thread.to}
Cc: {thread.cc}
Subject: {thread.subject}
Date: {thread.date}
Body:
{thread.body[:6000]}

Return JSON with:
body: the draft reply only
summary: one sentence about what the email needs
project: likely project name or null
priority: low, normal, or high
"""
        if self.api_budget:
            self.api_budget.consume("openai.chat.completions.create")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        return DraftResult(
            body=str(data["body"]).strip(),
            summary=str(data.get("summary", "")).strip(),
            project=data.get("project"),
            priority=str(data.get("priority", "normal")).strip(),
        )

    def brief(self, threads: list[EmailThread], events: list[dict], recent_memory: list[object]) -> str:
        if not self.client:
            return _template_brief(threads, events)

        emails = "\n\n".join(
            f"From: {t.sender}\nSubject: {t.subject}\nSnippet: {t.snippet}\nBody: {t.body[:1200]}"
            for t in threads
        )
        calendar = "\n".join(
            f"- {event.get('summary', '(no title)')} at {event.get('start', {})}"
            for event in events
        )
        memory = "\n".join(str(dict(row)) for row in recent_memory)
        if self.api_budget:
            self.api_budget.consume("openai.chat.completions.create")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": f"""
Create a concise morning brief for today.

Include:
- urgent or high-signal emails
- meetings and who they involve
- project reminders inferred from recent interactions
- suggested follow-ups

Emails:
{emails}

Calendar:
{calendar}

Recent vault interactions:
{memory}
"""}],
        )
        return response.choices[0].message.content.strip()

    def _template_reply(self, thread: EmailThread, memories: list[VaultMemory]) -> DraftResult:
        priority = _priority_from_thread(thread)
        greeting = _greeting_for(thread.sender)
        action = _action_sentence(thread, memories)
        body = (
            f"{greeting}\n\n"
            f"Thank you for the update regarding {thread.subject}. {action}"
            "\n\n"
            "Regards"
        )
        return DraftResult(
            body=body,
            summary=thread.snippet or thread.subject,
            project=None,
            priority=priority,
        )


def _template_brief(threads: list[EmailThread], events: list[dict]) -> str:
    lines = ["# Morning Brief", ""]
    lines.append("## Meetings")
    if events:
        for event in events:
            lines.append(f"- {event.get('summary', '(no title)')} - {event.get('start', {})}")
    else:
        lines.append("- No upcoming events found.")
    lines.extend(["", "## Email Highlights"])
    if threads:
        for thread in threads:
            lines.append(f"- {_priority_from_thread(thread).upper()} - {thread.sender}: {thread.subject}")
    else:
        lines.append("- No matching email highlights found.")
    return "\n".join(lines)


def _priority_from_thread(thread: EmailThread) -> str:
    text = f"{thread.subject} {thread.snippet} {thread.body}".lower()
    high_markers = [
        "urgent",
        "asap",
        "deadline",
        "due today",
        "submit by",
        "last date",
        "eod",
        "blocked",
        "waiting on you",
        "please confirm",
    ]
    low_markers = ["newsletter", "unsubscribe", "fyi", "no action needed"]
    if any(marker in text for marker in high_markers):
        return "high"
    if any(marker in text for marker in low_markers):
        return "low"
    return "normal"


def _greeting_for(sender: str) -> str:
    if not sender:
        return "Dear Sir/Madam,"
    name = sender.split("<", 1)[0].strip().strip('"')
    if not name or "@" in name:
        return "Dear Sir/Madam,"
    return f"Dear {name},"


def _action_sentence(thread: EmailThread, memories: list[VaultMemory] | None = None) -> str:
    text = f"{thread.subject} {thread.snippet} {thread.body}".lower()
    memory_text = " ".join(memory.note.lower() for memory in memories or [])
    if "never send automatically" in memory_text:
        approval_clause = "I will review this before taking any next step."
    else:
        approval_clause = "I will check this and get back shortly."
    if any(marker in text for marker in ["please confirm", "kindly confirm", "approval", "decision"]):
        return "I have noted that a confirmation or decision may be needed, and I will verify the details before responding definitively."
    if any(marker in text for marker in ["deadline", "submit by", "last date", "due today", "due tomorrow"]):
        return "I have noted the deadline and will check the requirements before taking the next step."
    if any(marker in text for marker in ["disseminate", "circulate", "share with", "forward"]):
        return "I have noted the request to circulate this and will check the appropriate channel before confirming."
    if "?" in text or any(marker in text for marker in ["can you", "could you", "would you", "let me know"]):
        return "I will review the question and get back with a clear response shortly."
    return f"I have noted the details. {approval_clause}"
