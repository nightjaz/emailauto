from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .api_budget import ApiCallBudget
from .vault import VaultMemory

if TYPE_CHECKING:
    from .google_services import EmailThread as GmailThread
    from .outlook_services import EmailThread as OutlookThread
    EmailThread = GmailThread | OutlookThread


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
        provider: str = "gemini",
    ):
        self.api_key = api_key
        self.model = model
        self.tone = tone
        self.user_name = user_name
        self.api_budget = api_budget
        self.provider = provider
        self.client = None

        if api_key:
            if provider == "gemini" or model.startswith("gemini"):
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                self.client = genai.GenerativeModel(model)
                self.provider = "gemini"
            else:
                from openai import OpenAI
                self.client = OpenAI(api_key=api_key)
                self.provider = "openai"

    def compose(self, thread, memories: list[VaultMemory]) -> DraftResult:
        if not self.client:
            return self._template_reply(thread, memories)

        memory_text = "\n".join(f"- {m.kind}:{m.key}: {m.note}" for m in memories) or "- none"
        prompt = f"""You are drafting an email reply for {self.user_name or 'the user'}.

FIRST, analyze the email:
1. What TYPE of email is this? (personal request, mass announcement, newsletter, direct question, FYI, etc.)
2. What SPECIFICALLY is the sender asking for or informing about?
3. Does this email ACTUALLY need a reply from me, or is it just informational?

THEN, if a reply is appropriate:
- Address the SPECIFIC ask (don't write generic "thank you for your email")
- If they asked a question, answer it or say you'll check
- If they requested action, confirm you'll do it or explain if you can't
- If it's an announcement/newsletter/mass email, either don't reply OR just acknowledge briefly

Tone: {self.tone}

Context about sender/projects:
{memory_text}

EMAIL TO REPLY TO:
From: {thread.sender}
Subject: {thread.subject}
Date: {thread.date}

{thread.body[:5000]}

Return ONLY valid JSON:
{{
  "needs_reply": true/false,
  "email_type": "what type of email this is",
  "what_they_want": "specific thing they're asking/informing",
  "body": "your draft reply (empty string if needs_reply is false)",
  "summary": "one sentence: what this email needs from you",
  "project": "project name or null",
  "priority": "low/normal/high"
}}

If needs_reply is false, body should be empty string "".
"""
        if self.api_budget:
            self.api_budget.consume("llm.generate")
        try:
            if self.provider == "gemini":
                response = self.client.generate_content(prompt)
                text = response.text
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                )
                text = response.choices[0].message.content

            data = _parse_json_response(text)

            body = str(data.get("body", "")).strip()
            needs_reply = data.get("needs_reply", True)

            if not needs_reply or not body:
                body = ""

            return DraftResult(
                body=body,
                summary=str(data.get("summary", data.get("what_they_want", ""))).strip(),
                project=data.get("project"),
                priority=str(data.get("priority", "normal")).strip(),
            )
        except Exception:
            return self._template_reply(thread, memories)

    def brief(self, threads: list, events: list[dict], recent_memory: list[object]) -> str:
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
            self.api_budget.consume("llm.generate")

        prompt = f"""
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
"""
        try:
            if self.provider == "gemini":
                response = self.client.generate_content(prompt)
                return response.text.strip()
            else:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.choices[0].message.content.strip()
        except Exception:
            return _template_brief(threads, events)

    def _template_reply(self, thread, memories: list[VaultMemory]) -> DraftResult:
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


def _parse_json_response(text: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
    return json.loads(text)


def _template_brief(threads: list, events: list[dict]) -> str:
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


def _priority_from_thread(thread) -> str:
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


def _action_sentence(thread, memories: list[VaultMemory] | None = None) -> str:
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
