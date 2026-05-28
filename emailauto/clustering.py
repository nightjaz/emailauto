from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .google_services import EmailThread as GmailThread
    from .outlook_services import EmailThread as OutlookThread
    EmailThread = GmailThread | OutlookThread


@dataclass
class EmailCluster:
    name: str
    description: str
    email_indices: list[int]

    @property
    def count(self) -> int:
        return len(self.email_indices)


class EmailClusterer:
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model = model
        self.client = None

        if api_key:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self.client = genai.GenerativeModel(model)

    def cluster_emails(self, threads: list) -> list[EmailCluster]:
        """
        Use Gemini to analyze emails and auto-detect categories.
        No predefined categories - let LLM discover them from content.
        """
        if not self.client or not threads:
            return []

        emails_text = "\n\n".join(
            f"[{i}] From: {t.sender}\nSubject: {t.subject}\nSnippet: {t.snippet[:200]}"
            for i, t in enumerate(threads)
        )

        prompt = f"""Analyze these emails and identify natural categories/clusters.
Group similar emails together based on their content, purpose, or type.

Emails:
{emails_text}

Return ONLY valid JSON (no markdown, no explanation) in this exact format:
{{"clusters": [{{"name": "Category Name", "description": "Brief description", "email_indices": [0, 2, 5]}}]}}

Guidelines:
- Discover 3-7 meaningful categories from the actual content
- Common patterns: Deadlines/Urgent, Action Required, Project Updates, Newsletters/FYI, Meetings, etc.
- Each email index should appear in exactly one cluster
- Use the indices [0, 1, 2, ...] from the email list above
"""

        try:
            response = self.client.generate_content(prompt)
            data = _parse_json_response(response.text)
            clusters = []
            for c in data.get("clusters", []):
                indices = c.get("email_indices", [])
                valid_indices = [i for i in indices if isinstance(i, int) and 0 <= i < len(threads)]
                if valid_indices:
                    clusters.append(EmailCluster(
                        name=str(c.get("name", "Other")),
                        description=str(c.get("description", "")),
                        email_indices=valid_indices,
                    ))
            return clusters
        except Exception:
            return _fallback_clustering(threads)

    def format_clusters_for_brief(self, clusters: list[EmailCluster], threads: list) -> str:
        """Format clusters into markdown for the brief."""
        if not clusters:
            return ""

        lines = ["## Email Categories (Auto-detected)", ""]
        for cluster in clusters:
            lines.append(f"### {cluster.name} ({cluster.count} emails)")
            if cluster.description:
                lines.append(f"*{cluster.description}*")
            lines.append("")
            for idx in cluster.email_indices[:5]:
                if idx < len(threads):
                    t = threads[idx]
                    sender_name = t.sender.split("<")[0].strip().strip('"') or t.sender
                    lines.append(f"- **{sender_name}**: {t.subject}")
            if cluster.count > 5:
                lines.append(f"- *...and {cluster.count - 5} more*")
            lines.append("")
        return "\n".join(lines)


def _parse_json_response(text: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
    return json.loads(text)


def _fallback_clustering(threads: list) -> list[EmailCluster]:
    """Simple rule-based clustering when LLM fails."""
    urgent = []
    action = []
    fyi = []

    for i, t in enumerate(threads):
        text = f"{t.subject} {t.snippet}".lower()
        if any(w in text for w in ["urgent", "deadline", "asap", "due today", "due tomorrow"]):
            urgent.append(i)
        elif any(w in text for w in ["please", "can you", "could you", "confirm", "?"]):
            action.append(i)
        else:
            fyi.append(i)

    clusters = []
    if urgent:
        clusters.append(EmailCluster("Urgent/Deadlines", "Time-sensitive emails", urgent))
    if action:
        clusters.append(EmailCluster("Action Required", "Emails needing your response", action))
    if fyi:
        clusters.append(EmailCluster("FYI/Informational", "No immediate action needed", fyi))
    return clusters


@dataclass
class EmailSummary:
    what: str
    deadline: str | None
    action: str | None
    needs_reply: bool


class EmailSummarizer:
    """Generate coherent, actionable summaries for emails."""

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model = model
        self.client = None

        if api_key:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self.client = genai.GenerativeModel(model)

    def summarize_emails(self, threads: list) -> dict[str, EmailSummary]:
        """Summarize multiple emails in one API call for efficiency."""
        if not self.client or not threads:
            return {}

        emails_text = "\n\n---\n\n".join(
            f"EMAIL_ID: {t.message_id}\nFrom: {t.sender}\nSubject: {t.subject}\nBody:\n{t.body[:1500]}"
            for t in threads
        )

        prompt = f"""Analyze these emails and provide a summary for each.

{emails_text}

For EACH email, return JSON with:
- email_id: the EMAIL_ID from above
- what: One clear sentence explaining what this email is about
- deadline: The specific deadline date/time if mentioned, or null
- action: What the recipient needs to DO (apply, respond, attend, etc), or null if just FYI
- needs_reply: true if sender expects a response, false if informational/newsletter

Return ONLY a JSON array, no markdown:
[{{"email_id": "...", "what": "...", "deadline": "...", "action": "...", "needs_reply": true}}]

Be specific. Don't say "an opportunity" - say "recruiting for Student Investment Team".
Don't say "deadline approaching" - say "Deadline: May 27, 11 PM".
"""

        try:
            response = self.client.generate_content(prompt)
            data = _parse_json_response(response.text)

            summaries = {}
            for item in data:
                email_id = item.get("email_id", "")
                summaries[email_id] = EmailSummary(
                    what=item.get("what", ""),
                    deadline=item.get("deadline"),
                    action=item.get("action"),
                    needs_reply=item.get("needs_reply", False),
                )
            return summaries
        except Exception:
            return {}

    def format_summary_for_brief(self, thread, summary: EmailSummary) -> list[str]:
        """Format a single email summary as bullet points."""
        sender_name = thread.sender.split("<")[0].strip().strip('"') or thread.sender
        lines = [f"- **{sender_name}**: {summary.what}"]

        if summary.deadline:
            lines.append(f"  - **Deadline:** {summary.deadline}")
        if summary.action:
            lines.append(f"  - **Action:** {summary.action}")
        elif not summary.needs_reply:
            lines.append(f"  - *No action needed*")

        return lines
