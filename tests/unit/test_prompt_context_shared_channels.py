from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.agent.core import prompt_context
from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    PersistentAgentDiscordChannelSubscription,
    PersistentAgentDiscordGuild,
)


@tag("batch_promptree")
class SharedChannelContextTests(TestCase):
    """A teammate who already sees the same channel messages should be visible as such."""

    def _make_agent(self, name: str) -> PersistentAgent:
        user = get_user_model().objects.create_user(
            username=f"{name}@example.test",
            email=f"{name}@example.test",
        )
        return PersistentAgent.objects.create(
            user=user,
            name=name,
            charter="Test charter.",
            browser_use_agent=BrowserUseAgent.objects.create(user=user, name=f"{name} browser"),
        )

    def _subscribe(self, agent: PersistentAgent, channel_id: str, channel_name: str) -> None:
        guild, _ = PersistentAgentDiscordGuild.objects.get_or_create(
            guild_id="guild-1",
            defaults={"name": "Guild", "owner_user": agent.user},
        )
        PersistentAgentDiscordChannelSubscription.objects.create(
            agent=agent,
            guild=guild,
            channel_id=channel_id,
            channel_name=channel_name,
        )

    def test_no_subscriptions_yields_nothing(self):
        agent = self._make_agent("solo")

        self.assertEqual(prompt_context._get_shared_channel_names(agent), {})

    def test_only_agents_sharing_a_channel_are_reported(self):
        agent = self._make_agent("owner")
        together = self._make_agent("together")
        apart = self._make_agent("apart")
        self._subscribe(agent, "chan-1", "infra")
        self._subscribe(together, "chan-1", "infra")
        self._subscribe(apart, "chan-2", "random")

        shared = prompt_context._get_shared_channel_names(agent)

        self.assertEqual(shared.get(together.id), ["#infra"])
        self.assertNotIn(apart.id, shared)
        self.assertNotIn(agent.id, shared)

    def test_disabled_subscriptions_do_not_count_as_presence(self):
        agent = self._make_agent("owner2")
        gone = self._make_agent("gone")
        self._subscribe(agent, "chan-3", "infra")
        self._subscribe(gone, "chan-3", "infra")
        PersistentAgentDiscordChannelSubscription.objects.filter(agent=gone).update(
            status=PersistentAgentDiscordChannelSubscription.Status.DISABLED,
        )

        self.assertEqual(prompt_context._get_shared_channel_names(agent), {})
