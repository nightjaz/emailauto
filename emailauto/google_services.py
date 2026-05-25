from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .api_budget import ApiCallBudget


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]


@dataclass(frozen=True)
class EmailThread:
    thread_id: str
    message_id: str
    sender: str
    to: str
    cc: str
    subject: str
    date: str
    body: str
    snippet: str


def get_credentials(credentials_path: Path, token_path: Path) -> Credentials:
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"Missing {credentials_path}. Download an OAuth desktop credentials file first."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(
                host="127.0.0.1",
                bind_addr="127.0.0.1",
                port=0,
                open_browser=False,
                timeout_seconds=300,
            )
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


class GoogleServices:
    def __init__(self, credentials_path: Path, token_path: Path, api_budget: ApiCallBudget | None = None):
        creds = get_credentials(credentials_path, token_path)
        self.gmail = build("gmail", "v1", credentials=creds)
        self.api_budget = api_budget

    def _execute(self, request, label: str) -> dict[str, Any]:
        if self.api_budget:
            self.api_budget.consume(label)
        return request.execute()

    def list_threads(self, query: str, limit: int) -> list[str]:
        response = self._execute(
            self.gmail.users()
            .threads()
            .list(userId="me", q=query, maxResults=max(0, limit)),
            "gmail.threads.list",
        )
        return [thread["id"] for thread in response.get("threads", [])]

    def get_thread(self, thread_id: str) -> EmailThread:
        raw_thread = self._execute(
            self.gmail.users()
            .threads()
            .get(userId="me", id=thread_id, format="full"),
            "gmail.threads.get",
        )
        messages = raw_thread.get("messages", [])
        latest = messages[-1]
        headers = {h["name"].lower(): h["value"] for h in latest["payload"].get("headers", [])}
        return EmailThread(
            thread_id=thread_id,
            message_id=latest["id"],
            sender=headers.get("from", ""),
            to=headers.get("to", ""),
            cc=headers.get("cc", ""),
            subject=headers.get("subject", "(no subject)"),
            date=headers.get("date", ""),
            body=_extract_body(latest.get("payload", {})),
            snippet=latest.get("snippet", ""),
        )

    def create_draft_reply(self, thread: EmailThread, body: str, signature: str = "") -> str:
        message = EmailMessage()
        _, address = parseaddr(thread.sender)
        message["To"] = address or thread.sender
        message["Subject"] = _reply_subject(thread.subject)
        if thread.cc:
            message["Cc"] = thread.cc
        if signature:
            body = f"{body.rstrip()}\n\n{signature.strip()}"
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        draft = self._execute(
            self.gmail.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw, "threadId": thread.thread_id}}),
            "gmail.drafts.create",
        )
        return draft["id"]


def _reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def _extract_body(payload: dict[str, Any]) -> str:
    parts = payload.get("parts", [])
    if payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for preferred in ("text/plain", "text/html"):
        found = _find_part(parts, preferred)
        if found:
            text = _decode(found.get("body", {}).get("data", ""))
            if preferred == "text/html":
                return _html_to_text(text)
            return text
    return ""


def _find_part(parts: list[dict[str, Any]], mime_type: str) -> dict[str, Any] | None:
    for part in parts:
        if part.get("mimeType") == mime_type:
            return part
        nested = _find_part(part.get("parts", []), mime_type)
        if nested:
            return nested
    return None


def _decode(data: str) -> str:
    if not data:
        return ""
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    # Dependency-free fallback: this is not a browser render, just readable context.
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)</p\s*>", "\n\n", text)
    text = re.sub(r"(?s)<.*?>", " ", text)
    return re.sub(r"[ \t]+", " ", html_module_unescape(text)).strip()


def html_module_unescape(value: str) -> str:
    return html.unescape(value)
