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
    credentials_path: Path
    token_path: Path
    vault_path: Path
    gmail_query: str
    model: str
    max_context_emails: int
    user_name: str
    user_signature: str
    default_tone: str
    max_draft_age_days: int
    max_api_calls: int
    openai_api_key: str | None


def load_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    data_dir = ROOT / "data"
    return Settings(
        root=ROOT,
        data_dir=data_dir,
        drafts_dir=ROOT / "drafts",
        briefs_dir=ROOT / "vault" / "daily",
        vault_dir=ROOT / "vault",
        credentials_path=ROOT / "credentials.json",
        token_path=ROOT / "token.json",
        vault_path=data_dir / "vault.sqlite3",
        gmail_query=os.getenv("EMAILAUTO_GMAIL_QUERY", "is:unread"),
        model=os.getenv("EMAILAUTO_MODEL", "gpt-4.1-mini"),
        max_context_emails=int(os.getenv("EMAILAUTO_MAX_CONTEXT_EMAILS", "12")),
        user_name=os.getenv("EMAILAUTO_USER_NAME", "").strip(),
        user_signature=os.getenv("EMAILAUTO_USER_SIGNATURE", "").strip(),
        default_tone=os.getenv("EMAILAUTO_DEFAULT_TONE", "warm, concise, professional"),
        max_draft_age_days=int(os.getenv("EMAILAUTO_MAX_DRAFT_AGE_DAYS", "3")),
        max_api_calls=int(os.getenv("EMAILAUTO_MAX_API_CALLS", "10")),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
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
