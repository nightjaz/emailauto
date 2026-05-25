from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class VaultMemory:
    kind: str
    key: str
    note: str
    updated_at: str


class Vault:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists memories (
                    kind text not null,
                    key text not null,
                    note text not null,
                    updated_at text not null,
                    primary key (kind, key, note)
                );

                create table if not exists interactions (
                    id integer primary key autoincrement,
                    message_id text,
                    sender text,
                    subject text,
                    summary text,
                    project text,
                    created_at text not null
                );
                """
            )

    def remember(self, kind: str, key: str, note: str) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                insert or replace into memories (kind, key, note, updated_at)
                values (?, ?, ?, ?)
                """,
                (kind, key.strip().lower(), note.strip(), updated_at),
            )

    def add_interaction(
        self,
        message_id: str,
        sender: str,
        subject: str,
        summary: str,
        project: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into interactions
                    (message_id, sender, subject, summary, project, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    sender,
                    subject,
                    summary,
                    project,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def memories_for(self, people: list[str], projects: list[str]) -> list[VaultMemory]:
        keys = {p.strip().lower() for p in people if p.strip()}
        project_keys = {p.strip().lower() for p in projects if p.strip()}
        rows: list[sqlite3.Row] = []
        with self.connect() as conn:
            for kind, values in (("person", keys), ("project", project_keys), ("global", {"default"})):
                for value in values:
                    rows.extend(
                        conn.execute(
                            """
                            select kind, key, note, updated_at
                            from memories
                            where kind = ? and key = ?
                            order by updated_at desc
                            """,
                            (kind, value),
                        ).fetchall()
                    )
        return [VaultMemory(**dict(row)) for row in rows]

    def recent_interactions(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                select sender, subject, summary, project, created_at
                from interactions
                order by created_at desc
                limit ?
                """,
                (limit,),
            ).fetchall()
