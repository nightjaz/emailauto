from __future__ import annotations

import json
import webbrowser
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from typing import Any

import msal
import requests

from .api_budget import ApiCallBudget


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read", "Mail.ReadWrite", "offline_access"]


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


def get_outlook_credentials(
    client_id: str,
    tenant_id: str,
    token_path: Path,
    refresh_token: str | None = None,
) -> dict[str, Any]:
    """
    Get Outlook credentials using MSAL.
    - If refresh_token provided (CI mode): use it to get new access token
    - Otherwise (local mode): interactive browser flow
    """
    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )

    if refresh_token:
        result = app.acquire_token_by_refresh_token(refresh_token, SCOPES)
        if "access_token" in result:
            return result
        raise RuntimeError(f"Token refresh failed: {result.get('error_description', result)}")

    if token_path.exists():
        cached = json.loads(token_path.read_text(encoding="utf-8"))
        if cached.get("refresh_token"):
            result = app.acquire_token_by_refresh_token(cached["refresh_token"], SCOPES)
            if "access_token" in result:
                _save_token(token_path, result)
                return result

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to create device flow: {flow.get('error_description', flow)}")

    print(f"\nTo sign in, visit: {flow['verification_uri']}")
    print(f"Enter the code: {flow['user_code']}\n")

    try:
        webbrowser.open(flow["verification_uri"])
    except Exception:
        pass

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Authentication failed: {result.get('error_description', result)}")

    _save_token(token_path, result)
    return result


def _save_token(path: Path, result: dict[str, Any]) -> None:
    path.write_text(
        json.dumps({
            "access_token": result.get("access_token"),
            "refresh_token": result.get("refresh_token"),
            "expires_in": result.get("expires_in"),
        }),
        encoding="utf-8",
    )


class OutlookServices:
    def __init__(
        self,
        client_id: str,
        tenant_id: str,
        token_path: Path,
        api_budget: ApiCallBudget | None = None,
        refresh_token: str | None = None,
    ):
        self.client_id = client_id
        self.tenant_id = tenant_id
        self.token_path = token_path
        self.api_budget = api_budget
        self.refresh_token = refresh_token
        self._access_token: str | None = None

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token

        result = get_outlook_credentials(
            self.client_id,
            self.tenant_id,
            self.token_path,
            self.refresh_token,
        )
        self._access_token = result["access_token"]
        return self._access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    def _execute(self, method: str, url: str, label: str, **kwargs) -> dict[str, Any]:
        if self.api_budget:
            self.api_budget.consume(label)
        response = requests.request(method, url, headers=self._headers(), **kwargs)
        response.raise_for_status()
        return response.json() if response.text else {}

    def list_threads(self, query: str, limit: int) -> list[str]:
        """List unique conversation IDs from messages matching query."""
        url = f"{GRAPH_BASE}/me/messages"
        params = {
            "$filter": query,
            "$top": max(limit * 2, 50),
            "$orderby": "receivedDateTime desc",
            "$select": "conversationId",
        }
        result = self._execute("GET", url, "outlook.messages.list", params=params)
        messages = result.get("value", [])
        seen = set()
        thread_ids = []
        for msg in messages:
            cid = msg.get("conversationId")
            if cid and cid not in seen:
                seen.add(cid)
                thread_ids.append(cid)
            if len(thread_ids) >= limit:
                break
        return thread_ids

    def get_thread(self, thread_id: str) -> EmailThread:
        """Get the latest message from a conversation thread."""
        url = f"{GRAPH_BASE}/me/messages"
        params = {
            "$filter": f"conversationId eq '{thread_id}'",
            "$orderby": "receivedDateTime desc",
            "$top": 1,
            "$select": "id,conversationId,from,toRecipients,ccRecipients,subject,receivedDateTime,body,bodyPreview",
        }
        result = self._execute("GET", url, "outlook.messages.get", params=params)
        messages = result.get("value", [])
        if not messages:
            raise ValueError(f"No messages found for conversation {thread_id}")

        msg = messages[0]
        sender_data = msg.get("from", {}).get("emailAddress", {})
        sender = f"{sender_data.get('name', '')} <{sender_data.get('address', '')}>"

        to_list = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("toRecipients", [])
        ]
        cc_list = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("ccRecipients", [])
        ]

        body_content = msg.get("body", {}).get("content", "")
        if msg.get("body", {}).get("contentType") == "html":
            body_content = _html_to_text(body_content)

        return EmailThread(
            thread_id=thread_id,
            message_id=msg.get("id", ""),
            sender=sender.strip(),
            to=", ".join(to_list),
            cc=", ".join(cc_list),
            subject=msg.get("subject", "(no subject)"),
            date=msg.get("receivedDateTime", ""),
            body=body_content,
            snippet=msg.get("bodyPreview", ""),
        )

    def create_draft_reply(self, thread: EmailThread, body: str, signature: str = "") -> str:
        """Create a draft reply to a message."""
        reply_url = f"{GRAPH_BASE}/me/messages/{thread.message_id}/createReply"
        draft_result = self._execute("POST", reply_url, "outlook.messages.createReply")
        draft_id = draft_result.get("id")
        if not draft_id:
            raise RuntimeError("Failed to create reply draft")

        if signature:
            body = f"{body.rstrip()}\n\n{signature.strip()}"

        update_url = f"{GRAPH_BASE}/me/messages/{draft_id}"
        self._execute(
            "PATCH",
            update_url,
            "outlook.messages.update",
            json={"body": {"contentType": "text", "content": body}},
        )
        return draft_id


def _html_to_text(html: str) -> str:
    """Simple HTML to text conversion."""
    import re
    import html as html_module

    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)</p\s*>", "\n\n", text)
    text = re.sub(r"(?s)<.*?>", " ", text)
    text = re.sub(r"[ \t]+", " ", html_module.unescape(text))
    return text.strip()
