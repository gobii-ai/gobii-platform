from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag, override_settings

from api.management.commands.run_imap_idlers import _eligible_idle_accounts_queryset
from api.models import (
    AgentEmailAccount,
    BrowserUseAgent,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    UserQuota,
)


@tag("batch_email")
class ImapIdleRunnerEnvFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="imap-idle-env@example.com",
            email="imap-idle-env@example.com",
            password="pw",
        )
        quota, _ = UserQuota.objects.get_or_create(user=cls.user)
        quota.agent_limit = 100
        quota.save()

        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            local_browser_agent = BrowserUseAgent.objects.create(
                user=cls.user,
                name="imap-idle-env-browser-local",
            )
            staging_browser_agent = BrowserUseAgent.objects.create(
                user=cls.user,
                name="imap-idle-env-browser-staging",
            )

        cls.local_agent = PersistentAgent.objects.create(
            user=cls.user,
            name="local-agent",
            charter="Test",
            browser_use_agent=local_browser_agent,
            execution_environment="local",
        )
        cls.staging_agent = PersistentAgent.objects.create(
            user=cls.user,
            name="staging-agent",
            charter="Test",
            browser_use_agent=staging_browser_agent,
            execution_environment="staging",
        )

        cls.local_account = cls._create_account_for_agent(
            cls.local_agent,
            "local-agent@example.org",
        )
        cls.staging_account = cls._create_account_for_agent(
            cls.staging_agent,
            "staging-agent@example.org",
        )

    @classmethod
    def _create_account_for_agent(
        cls,
        agent: PersistentAgent,
        address: str,
    ) -> AgentEmailAccount:
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address=address,
            is_primary=True,
        )
        return AgentEmailAccount.objects.create(
            endpoint=endpoint,
            imap_host="imap.example.org",
            imap_port=993,
            imap_security=AgentEmailAccount.ImapSecurity.SSL,
            imap_username="user",
            is_inbound_enabled=True,
            imap_idle_enabled=True,
            poll_interval_sec=30,
        )

    @override_settings(GOBII_RELEASE_ENV="local")
    def test_queryset_only_includes_current_env_accounts(self):
        eligible_ids = set(
            _eligible_idle_accounts_queryset().values_list("pk", flat=True)
        )

        self.assertIn(self.local_account.pk, eligible_ids)
        self.assertNotIn(self.staging_account.pk, eligible_ids)
