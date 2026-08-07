"""deliver_results tool + freeze-wall redaction contract.

The teaser paywall's security boundary is the SERVER: while an agent sits at
the signup freeze, result rows past the teaser count leave the API redacted
(initials only, no detail/url). These tests pin that boundary.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.comms.message_service import build_web_agent_address, build_web_user_address
from api.agent.tools.deliver_results import (
    DELIVER_RESULTS_TOOL_NAME,
    _normalize_rows,
    get_deliver_results_tool,
)
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
)
from console.agent_chat.timeline import fetch_timeline_window


@tag("batch_agent_chat")
class DeliverResultsToolTests(TestCase):
    def test_tool_registered_and_bundled_with_sourcing_skill(self):
        from api.agent.system_skills.defaults import RECRUITMENT_SOURCING_SYSTEM_SKILL
        from api.agent.tools.tool_manager import BUILTIN_TOOL_REGISTRY

        self.assertIn(DELIVER_RESULTS_TOOL_NAME, BUILTIN_TOOL_REGISTRY)
        self.assertIn(DELIVER_RESULTS_TOOL_NAME, RECRUITMENT_SOURCING_SYSTEM_SKILL.tool_names)
        definition = get_deliver_results_tool()
        self.assertEqual(definition["function"]["name"], DELIVER_RESULTS_TOOL_NAME)
        self.assertIn("results", definition["function"]["parameters"]["properties"])

    def test_normalize_rows_drops_empty_and_caps_fields(self):
        rows = _normalize_rows(
            [
                {"primary": "  Ada Lovelace ", "secondary": "Engineer · Analytical", "score": "95%"},
                {"primary": ""},
                "not-a-dict",
                {"detail": "no primary"},
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["primary"], "Ada Lovelace")
        self.assertNotIn("url", rows[0])
        self.assertIsNone(_normalize_rows([]))
        self.assertIsNone(_normalize_rows("nope"))


@tag("batch_agent_chat")
class ResultsRedactionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.user = user_model.objects.create_user(
            username="results-owner",
            email="results-owner@example.com",
            password="password123",
        )
        cls.browser_agent = BrowserUseAgent.objects.create(user=cls.user, name="Results Browser")
        cls.agent = PersistentAgent.objects.create(
            user=cls.user,
            name="Results Agent",
            charter="Source candidates",
            browser_use_agent=cls.browser_agent,
            signup_preview_state=PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION,
        )
        agent_address = build_web_agent_address(cls.agent.id)
        user_address = build_web_user_address(cls.user.id, cls.agent.id)
        cls.agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.WEB,
            address=agent_address,
            is_primary=True,
        )
        cls.user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=user_address,
        )
        cls.conversation = PersistentAgentConversation.objects.create(
            owner_agent=cls.agent,
            channel=CommsChannel.WEB,
            address=user_address,
        )
        cls.rows = [
            {"primary": f"Candidate Number{i}", "secondary": f"Role {i} · Co {i}", "detail": f"evidence {i}", "score": "9{i}%", "url": f"https://example.com/{i}"}
            for i in range(1, 6)
        ]
        cls.message = PersistentAgentMessage.objects.create(
            owner_agent=cls.agent,
            from_endpoint=cls.agent_endpoint,
            to_endpoint=cls.user_endpoint,
            conversation=cls.conversation,
            is_outbound=True,
            body="**5 candidates — first pass**",
            raw_payload={"source": "web_chat_tool", "gobii_results": {"title": "First pass", "rows": cls.rows}},
        )

    def _results_envelope(self):
        window = fetch_timeline_window(self.agent, limit=10, viewer_user=self.user)
        for event in window.events:
            message = event.get("message") if isinstance(event, dict) else None
            if message and message.get("results"):
                return message["results"]
        return None

    def test_frozen_agent_gets_teaser_plus_redacted_rows(self):
        results = self._results_envelope()
        self.assertIsNotNone(results)
        self.assertEqual(results["lockedCount"], 2)
        rows = results["rows"]
        self.assertEqual(len(rows), 5)
        for row in rows[:3]:
            self.assertNotIn("locked", row)
            self.assertIn("url", row)
        for row in rows[3:]:
            self.assertTrue(row["locked"])
            # Redacted server-side: initials only, no detail, no url, and the
            # real name must not appear anywhere in the row.
            self.assertNotIn("url", row)
            self.assertNotIn("detail", row)
            self.assertNotIn("Candidate", row["primary"])
            self.assertLessEqual(len(row["primary"]), 6)

    def test_unfrozen_agent_gets_full_rows(self):
        PersistentAgent.objects.filter(id=self.agent.id).update(
            signup_preview_state=PersistentAgent.SignupPreviewState.NONE,
        )
        self.agent.refresh_from_db()
        results = self._results_envelope()
        self.assertIsNotNone(results)
        self.assertEqual(results["lockedCount"], 0)
        self.assertTrue(all("locked" not in row for row in results["rows"]))
        self.assertEqual(results["rows"][4]["primary"], "Candidate Number5")
