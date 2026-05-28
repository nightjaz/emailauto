from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    drafts_dir: Path
    briefs_dir: Path
    vault_dir: Path
    # Gmail (legacy)
    credentials_path: Path
    token_path: Path
    gmail_query: str
    # Outlook
    outlook_client_id: str | None
    outlook_tenant_id: str
    outlook_refresh_token: str | None
    outlook_token_path: Path
    outlook_query: str
    # Provider selection
    email_provider: str
    # Vault
    vault_path: Path
    # LLM
    model: str
    gemini_api_key: str | None
    openai_api_key: str | None
    # General
    max_context_emails: int
    user_name: str
    user_signature: str
    default_tone: str
    max_draft_age_days: int
    max_api_calls: int


def load_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    data_dir = ROOT / "data"
    return Settings(
        root=ROOT,
        data_dir=data_dir,
        drafts_dir=ROOT / "drafts",
        briefs_dir=ROOT / "vault" / "daily",
        vault_dir=ROOT / "vault",
        # Gmail (legacy)
        credentials_path=ROOT / "credentials.json",
        token_path=ROOT / "token.json",
        gmail_query=os.getenv("EMAILAUTO_GMAIL_QUERY", "is:unread"),
        # Outlook
        outlook_client_id=os.getenv("OUTLOOK_CLIENT_ID") or None,
        outlook_tenant_id=os.getenv("OUTLOOK_TENANT_ID", "consumers"),
        outlook_refresh_token=os.getenv("OUTLOOK_REFRESH_TOKEN") or None,
        outlook_token_path=ROOT / "outlook_token.json",
        outlook_query=os.getenv("EMAILAUTO_OUTLOOK_QUERY", "isRead eq false"),
        # Provider selection (outlook or gmail)
        email_provider=os.getenv("EMAILAUTO_PROVIDER", "outlook"),
        # Vault
        vault_path=data_dir / "vault.sqlite3",
        # LLM
        model=os.getenv("EMAILAUTO_MODEL", "gemini-2.0-flash"),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        # General
        max_context_emails=int(os.getenv("EMAILAUTO_MAX_CONTEXT_EMAILS", "12")),
        user_name=os.getenv("EMAILAUTO_USER_NAME", "").strip(),
        user_signature=os.getenv("EMAILAUTO_USER_SIGNATURE", "").strip(),
        default_tone=os.getenv("EMAILAUTO_DEFAULT_TONE", "warm, concise, professional"),
        max_draft_age_days=int(os.getenv("EMAILAUTO_MAX_DRAFT_AGE_DAYS", "3")),
        max_api_calls=int(os.getenv("EMAILAUTO_MAX_API_CALLS", "10")),
    )


def _load_dotenv(path: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        if not path.exists():
            return
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return
    load_dotenv(path)
