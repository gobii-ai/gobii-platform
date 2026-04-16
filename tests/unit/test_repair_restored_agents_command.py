from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag

from config import settings

from api.models import (
    AgentPeerLink,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
    UserQuota,
)


def create_browser_agent_without_proxy(user, name):
    with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
        return BrowserUseAgent.objects.create(user=user, name=name)


@tag("batch_api_persistent_agents")
class RepairRestoredAgentsCommandTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="repair-command@example.com",
            email="repair-command@example.com",
            password="password123",
        )
        quota, _ = UserQuota.objects.get_or_create(user=self.user)
        quota.agent_limit = 100
        quota.save(update_fields=["agent_limit"])

    def _create_agent(self, name: str) -> PersistentAgent:
        browser_agent = create_browser_agent_without_proxy(self.user, f"{name}-browser")
        return PersistentAgent.objects.create(
            user=self.user,
            name=name,
            charter=f"{name} charter",
            browser_use_agent=browser_agent,
        )

    def test_command_dry_run_then_apply_repairs_pre_snapshot_damage(self):
        agent = self._create_agent("Repair Target")
        active_peer = self._create_agent("Repair Active Peer")
        deleted_peer = self._create_agent("Repair Deleted Peer")

        alias_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.EMAIL,
            address=f"repair.target@{settings.DEFAULT_AGENT_EMAIL_DOMAIN}",
            is_primary=False,
        )
        agent_peer_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=None,
            channel=CommsChannel.OTHER,
            address=f"peer://agent/{agent.id}",
            is_primary=False,
        )
        active_peer_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=active_peer,
            channel=CommsChannel.OTHER,
            address=f"peer://agent/{active_peer.id}",
            is_primary=False,
        )
        PersistentAgentCommsEndpoint.objects.create(
            owner_agent=deleted_peer,
            channel=CommsChannel.OTHER,
            address=f"peer://agent/{deleted_peer.id}",
            is_primary=False,
        )

        email_conversation = PersistentAgentConversation.objects.create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address="recipient@example.com",
        )
        PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=alias_endpoint,
            conversation=email_conversation,
            owner_agent=agent,
            body="Repair email proof",
        )

        active_pair_key = AgentPeerLink.build_pair_key(agent.id, active_peer.id)
        active_conversation = PersistentAgentConversation.objects.create(
            channel=CommsChannel.OTHER,
            address=f"peer://{active_pair_key}",
            is_peer_dm=False,
        )

        deleted_pair_key = AgentPeerLink.build_pair_key(agent.id, deleted_peer.id)
        deleted_conversation = PersistentAgentConversation.objects.create(
            channel=CommsChannel.OTHER,
            address=f"peer://{deleted_pair_key}",
            is_peer_dm=False,
        )
        deleted_peer.soft_delete()

        dry_run = StringIO()
        call_command(
            "repair_restored_agents",
            "--agent-id",
            str(agent.id),
            stdout=dry_run,
        )

        alias_endpoint.refresh_from_db()
        agent_peer_endpoint.refresh_from_db()
        active_conversation.refresh_from_db()
        deleted_conversation.refresh_from_db()
        self.assertIsNone(alias_endpoint.owner_agent_id)
        self.assertFalse(alias_endpoint.is_primary)
        self.assertIsNone(agent_peer_endpoint.owner_agent_id)
        self.assertIsNone(active_conversation.peer_link_id)
        self.assertFalse(active_conversation.is_peer_dm)
        self.assertIn("Would repair agent", dry_run.getvalue())

        apply_run = StringIO()
        call_command(
            "repair_restored_agents",
            "--agent-id",
            str(agent.id),
            "--apply",
            stdout=apply_run,
        )

        alias_endpoint.refresh_from_db()
        agent_peer_endpoint.refresh_from_db()
        active_conversation.refresh_from_db()
        deleted_conversation.refresh_from_db()

        restored_link = AgentPeerLink.objects.get(pair_key=active_pair_key)

        self.assertEqual(alias_endpoint.owner_agent_id, agent.id)
        self.assertTrue(alias_endpoint.is_primary)
        self.assertEqual(agent_peer_endpoint.owner_agent_id, agent.id)
        self.assertEqual(active_conversation.peer_link_id, restored_link.id)
        self.assertTrue(active_conversation.is_peer_dm)
        self.assertFalse(AgentPeerLink.objects.filter(pair_key=deleted_pair_key).exists())
        self.assertIsNone(deleted_conversation.peer_link_id)
        self.assertFalse(deleted_conversation.is_peer_dm)
        self.assertSetEqual(
            {restored_link.agent_a_endpoint_id, restored_link.agent_b_endpoint_id},
            {agent_peer_endpoint.id, active_peer_endpoint.id},
        )
        self.assertIn("skipped", apply_run.getvalue())
