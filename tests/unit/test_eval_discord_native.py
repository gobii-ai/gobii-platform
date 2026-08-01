from types import SimpleNamespace

from django.test import SimpleTestCase, tag

from api.evals.scenarios.discord_native import _agent_addressed_reply_context


@tag("batch_eval_fingerprint")
class DiscordNativeEvalFixtureTests(SimpleTestCase):
    def test_reply_context_addresses_the_agent_under_evaluation(self):
        agent = SimpleNamespace(id="agent-123", name="Jordan")

        context = _agent_addressed_reply_context(
            agent,
            channel_id="channel-1",
            guild_id="guild-1",
        )

        self.assertEqual(context["author_id"], "eval-agent-agent-123")
        self.assertEqual(context["author_name"], "Jordan")
        self.assertNotEqual(context["author_name"], "Maya")
