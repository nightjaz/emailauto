from __future__ import annotations

import unittest

from emailauto.google_services import EmailThread
from emailauto.llm import DraftComposer
from emailauto.vault import VaultMemory


class TemplateDraftTest(unittest.TestCase):
    def test_saved_context_does_not_leak_into_draft_body(self) -> None:
        thread = EmailThread(
            thread_id="thread-1",
            message_id="message-1",
            sender="Prajakta Bandgar <prajakta.bandgar106@gmail.com>",
            to="Prajakta <user@example.com>",
            cc="",
            subject='Fwd: [GitHub] @BlazingFire27 made you an owner of the "Rocketry Avionics" organization',
            date="Mon, 25 May 2026 09:00:00 +0000",
            body="Please review this forwarded GitHub ownership notification.",
            snippet="Please review this forwarded GitHub ownership notification.",
        )
        memories = [
            VaultMemory(
                kind="global",
                key="default",
                note="Keep replies concise and never send automatically.",
                updated_at="2026-05-25T09:00:00",
            )
        ]

        draft = DraftComposer(None, "unused", "warm, concise").compose(thread, memories)

        self.assertNotIn("saved context", draft.body.lower())
        self.assertNotIn("before i send", draft.body.lower())
        self.assertNotIn("global:default", draft.body.lower())


if __name__ == "__main__":
    unittest.main()
