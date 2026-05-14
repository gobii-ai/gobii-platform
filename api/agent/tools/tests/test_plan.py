from types import SimpleNamespace

from django.test import SimpleTestCase, tag

from api.agent.tools.plan import execute_update_plan, get_update_plan_tool


@tag("batch_agent_tools")
class UpdatePlanValidationTests(SimpleTestCase):
    agent = SimpleNamespace(id="agent-123")

    def test_message_deliverable_schema_excludes_peer_messages(self):
        tool = get_update_plan_tool()
        messages_description = tool["function"]["parameters"]["properties"]["messages"]["description"]
        message_id_description = (
            tool["function"]["parameters"]["properties"]["messages"]["items"]["properties"]["message_id"]["description"]
        )

        self.assertIn("send_email", messages_description)
        self.assertIn("send_sms", messages_description)
        self.assertIn("send_chat_message", messages_description)
        self.assertIn("Do not include peer messages", messages_description)
        self.assertIn("Exact UUID", message_id_description)
        self.assertIn("never use placeholders", message_id_description)

    def test_invalid_message_deliverable_feedback_explains_user_facing_only(self):
        result = execute_update_plan(
            self.agent,
            {
                "plan": [{"step": "Send update", "status": "done"}],
                "messages": [
                    {
                        "label": "Peer update",
                        "message_id": "peer://51ee7718-e3a4-43b6-a88d-67d0a8bd346c::agent-123",
                    }
                ],
                "will_continue_work": False,
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("messages[0].message_id must be a valid UUID", result["message"])
        self.assertIn("Use messages only for final user-facing deliveries", result["message"])
        self.assertIn("send_email", result["message"])
        self.assertIn("send_sms", result["message"])
        self.assertIn("send_chat_message", result["message"])
        self.assertIn("Do not include peer messages from send_agent_message", result["message"])
