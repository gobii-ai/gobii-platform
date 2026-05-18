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
        self.assertIn("not for every quick answer", messages_description)
        self.assertIn("send it first with will_continue_work=true", messages_description)
        self.assertIn("then call update_plan after the send tool returns", messages_description)
        self.assertIn("Do not include peer messages", messages_description)
        self.assertIn("Exact UUID", message_id_description)
        self.assertIn("never use placeholders", message_id_description)

    def test_tool_description_guides_plan_reset_for_new_iterations(self):
        tool = get_update_plan_tool()
        description = tool["function"]["description"]

        self.assertIn("full current active plan", description)
        self.assertIn("usually 3-6 active steps", description)
        self.assertIn("omit stale prior-task or prior-run steps", description)
        self.assertIn("new scheduled run", description)
        self.assertIn("do not create one step per day, hour, or recurrence slot", description)
        self.assertIn("represent the current run with compact reusable phases", description)

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
        self.assertIn("Use messages only for substantial final deliverables", result["message"])
        self.assertIn("send_email", result["message"])
        self.assertIn("send_sms", result["message"])
        self.assertIn("send_chat_message", result["message"])
        self.assertIn("send it first with will_continue_work=true", result["message"])
        self.assertIn("Do not include peer messages from send_agent_message", result["message"])
