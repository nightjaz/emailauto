from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from emailauto.api_budget import ApiCallBudget, ApiCallBudgetExceeded
from emailauto.google_services import EmailThread
from emailauto.pipeline import (
    _brief_points,
    _brief_sender_name,
    _needs_response,
    _should_create_draft,
    _should_include_in_brief,
    _thread_fetch_limit,
)


def thread(
    subject: str,
    body: str,
    *,
    sender: str = "Office <office@example.com>",
    days_old: int = 0,
) -> EmailThread:
    sent_at = datetime.now(timezone.utc) - timedelta(days=days_old)
    return EmailThread(
        thread_id="thread-1",
        message_id="message-1",
        sender=sender,
        to="Prajakta <user@example.com>",
        cc="",
        subject=subject,
        date=sent_at.strftime("%a, %d %b %Y %H:%M:%S %z"),
        body=body,
        snippet=body[:180],
    )


class PipelineClassificationTest(unittest.TestCase):
    def test_api_budget_enforces_hard_call_limit(self) -> None:
        budget = ApiCallBudget(2)

        budget.consume("first")
        budget.consume("second")

        with self.assertRaises(ApiCallBudgetExceeded):
            budget.consume("third")

    def test_thread_fetch_limit_keeps_calls_under_budget(self) -> None:
        budget = ApiCallBudget(10)

        self.assertEqual(_thread_fetch_limit(20, budget), 9)
        self.assertEqual(_thread_fetch_limit(20, budget, reserve_calls=2), 7)

    def test_broadcast_announcement_does_not_need_reply(self) -> None:
        email = thread(
            "Democratised agriculture hackathon",
            "Dear students, please find information about this hackathon. Deadline: 5 June 2026.",
        )

        self.assertFalse(_needs_response(email))

    def test_deadline_only_update_does_not_need_reply(self) -> None:
        email = thread(
            "Summer Term 2025-26 updates",
            "Dear Students, updated schedules have been uploaded. Important: deadline is tomorrow.",
        )

        self.assertFalse(_needs_response(email))

    def test_direct_confirmation_request_needs_reply(self) -> None:
        email = thread(
            "Project meeting",
            "Could you please confirm whether you can attend the meeting tomorrow?",
            sender="Asha Rao <asha@example.com>",
        )

        self.assertTrue(_needs_response(email))

    def test_indirect_deadline_question_needs_reply(self) -> None:
        email = thread(
            "project verification",
            "Hi prajakta, i wanted to know when you would be able to hand in your mid-semester reports. thankyou",
            sender="Prajakta Bandgar <prajakta.bandgar106@gmail.com>",
        )

        self.assertTrue(_needs_response(email))

    def test_stale_unread_message_does_not_create_draft(self) -> None:
        email = thread(
            "Project meeting",
            "Could you please confirm whether you can attend?",
            sender="Asha Rao <asha@example.com>",
            days_old=11,
        )
        settings = SimpleNamespace(max_draft_age_days=3)

        self.assertFalse(_should_create_draft(settings, email))

    def test_fresh_fyi_message_can_appear_in_brief(self) -> None:
        email = thread(
            "Gym timings during summer vacation",
            "Dear All, SAC timings during the summer vacation will be 6 AM to 10 AM.",
        )

        self.assertTrue(_should_include_in_brief(email))

    def test_old_low_importance_message_does_not_appear_in_brief(self) -> None:
        email = thread(
            "Gym timings during summer vacation",
            "Dear All, SAC timings during the summer vacation will be 6 AM to 10 AM.",
            days_old=11,
        )

        self.assertFalse(_should_include_in_brief(email))

    def test_old_direct_request_still_appears_in_brief(self) -> None:
        email = thread(
            "Project meeting",
            "Could you please confirm whether you can attend the meeting tomorrow?",
            sender="Asha Rao <asha@example.com>",
            days_old=11,
        )

        self.assertTrue(_should_include_in_brief(email))

    def test_very_old_urgent_deadline_request_still_appears_in_brief(self) -> None:
        email = thread(
            "Proposal deadline",
            "Urgent: could you please confirm your approval? The proposal deadline is today.",
            sender="Asha Rao <asha@example.com>",
            days_old=21,
        )

        self.assertTrue(_should_include_in_brief(email))

    def test_brief_points_extract_dates_and_details_instead_of_greeting(self) -> None:
        email = thread(
            "Democratised agriculture hackathon",
            "\n".join(
                [
                    "Dear students Good morning",
                    "Please find information about Democratised agriculture hackathon being organised by Imperial College London.",
                    "Last date to register and team formation: 30th May, 2026",
                    "Interim submission: 15th June, 2026",
                    "Final submission: 15th July, 2026",
                    "Final evaluation: 30th July, 2026",
                    "Multi-disciplinary and multi-institutional teams with expertise in flexible sensors and electronics, robotics, drones, agriculture, AI.",
                ]
            ),
            sender="Dean SWD Office BITS Pilani <deanswd.office@bits-pilani.ac.in>",
        )

        points = _brief_points(email)

        self.assertNotIn("Dear students Good morning", points)
        self.assertTrue(any("30th May, 2026" in point for point in points))
        self.assertTrue(any("Imperial College London" in point for point in points))

    def test_brief_sender_name_hides_email_address(self) -> None:
        email = thread(
            "Summer Term 2025-26 updates",
            "The deadline to edit Google Form responses is 15 May 2026, 5:00 PM.",
            sender="Instruction Office <instruction.office@goa.bits-pilani.ac.in>",
        )

        self.assertEqual(_brief_sender_name(email), "Instruction Office")

    def test_github_url_query_does_not_make_notification_waiting_on_me(self) -> None:
        email = thread(
            'Fwd: [GitHub] @BlazingFire27 made you an owner of the "Rocketry Avionics" organization',
            "\n".join(
                [
                    "From: Rocketry Avionics <noreply@github.com>",
                    "Date: Tue, May 19, 2026 at 7:32 PM",
                    'Subject: [GitHub] @BlazingFire27 made you an owner of the "Rocketry Avionics" organization',
                    "To: nightjaz <prajakta.bandgar106@gmail.com>",
                    "[image: GitHub] You have been made an owner of the Rocketry Avionics organization",
                    "Hi @nightjaz",
                    'You have been made an owner of the "Rocketry Avionics" organization.',
                    "Owners have full rights to the organization and have complete access to all repositories and teams.",
                    "You can view all organization owners at https://github.com/orgs/Rocketry-Avionics/people?query=role%3Aowner",
                    "Read more about organization permissions at https://docs.github.com/articles/what-are-the-different-access-permissions/#organization-accounts",
                    "You’re receiving this email because you were made an owner of an organization on GitHub.",
                    "GitHub, Inc. 88 Colin P Kelly Jr Street San Francisco, CA 94107",
                ]
            ),
            sender="Prajakta Bandgar <prajakta.bandgar106@gmail.com>",
        )

        self.assertFalse(_needs_response(email))
        self.assertEqual(_brief_sender_name(email), "Rocketry Avionics")

        points = _brief_points(email)
        joined = "\n".join(points).lower()
        self.assertNotIn("https://", joined)
        self.assertNotIn("to:", joined)
        self.assertNotIn("from:", joined)
        self.assertNotIn("date:", joined)
        self.assertIn(
            "Owners have full rights to the organization and have complete access to all repositories and teams.",
            points,
        )
        self.assertLessEqual(len(points), 2)


if __name__ == "__main__":
    unittest.main()
