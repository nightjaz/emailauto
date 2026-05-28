from __future__ import annotations

import argparse
import sys

from .config import load_settings
from .vault import Vault


def main() -> None:
    _configure_stdout()
    parser = argparse.ArgumentParser(prog="emailauto")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize local storage.")
    subparsers.add_parser("auth", help="Authorize Google/Gmail access and save token.json.")
    subparsers.add_parser("auth-outlook", help="Authorize Outlook access and save outlook_token.json.")

    scan = subparsers.add_parser("scan", help="Preview or create drafts for pending email.")
    scan.add_argument("--limit", type=int, default=10)
    scan.add_argument("--dry-run", action="store_true")
    scan.add_argument("--create-drafts", action="store_true")

    brief = subparsers.add_parser("brief", help="Generate an email brief without creating drafts.")
    brief.add_argument("--email-limit", type=int, default=9)
    brief.add_argument("--label", default="morning-brief")

    run = subparsers.add_parser("run", help="Generate a brief, update TODO/project context, and create email drafts.")
    run.add_argument("--email-limit", type=int, default=9)
    run.add_argument("--label", default="morning-brief")

    remember = subparsers.add_parser("remember", help="Store a preference in the vault.")
    remember.add_argument("kind", choices=["person", "project", "global"])
    remember.add_argument("key")
    remember.add_argument("note")

    args = parser.parse_args()
    settings = load_settings()

    if args.command == "init":
        Vault(settings.vault_path).init()
        settings.briefs_dir.mkdir(parents=True, exist_ok=True)
        print(f"Initialized vault at {settings.vault_path}")
        return

    if args.command == "auth":
        from .google_services import get_credentials

        try:
            get_credentials(settings.credentials_path, settings.token_path)
        except FileNotFoundError as exc:
            parser.error(str(exc))
        print(f"Authorized Google access and saved {settings.token_path}")
        return

    if args.command == "auth-outlook":
        from .outlook_services import get_outlook_credentials

        if not settings.outlook_client_id:
            parser.error("OUTLOOK_CLIENT_ID not set. Configure it in .env or environment.")
        try:
            result = get_outlook_credentials(
                settings.outlook_client_id,
                settings.outlook_tenant_id,
                settings.outlook_token_path,
            )
            print(f"Authorized Outlook access and saved {settings.outlook_token_path}")
            if result.get("refresh_token"):
                print(f"\nRefresh token for GitHub Secrets (OUTLOOK_REFRESH_TOKEN):")
                print(result["refresh_token"][:50] + "..." if len(result.get("refresh_token", "")) > 50 else result.get("refresh_token", ""))
                print("\n(Full token saved in outlook_token.json)")
        except Exception as exc:
            parser.error(str(exc))
        return

    if args.command == "remember":
        Vault(settings.vault_path).remember(args.kind, args.key, args.note)
        print(f"Remembered {args.kind}:{args.key}")
        return

    if args.command == "scan":
        from .pipeline import run_scan

        if not args.dry_run and not args.create_drafts:
            args.dry_run = True
        try:
            for line in run_scan(settings, args.limit, args.dry_run, args.create_drafts):
                print(line)
        except FileNotFoundError as exc:
            parser.error(str(exc))
        return

    if args.command == "brief":
        from .pipeline import run_brief

        try:
            path = run_brief(settings, args.email_limit, args.label)
        except FileNotFoundError as exc:
            parser.error(str(exc))
        print(f"Wrote brief to {path}")
        return

    if args.command == "run":
        from .pipeline import run_workflow

        try:
            path, draft_logs = run_workflow(settings, args.email_limit, args.label)
        except FileNotFoundError as exc:
            parser.error(str(exc))
        print(f"Wrote brief to {path}")
        print(f"Created or skipped {len(draft_logs)} Gmail draft item(s).")


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
