import json
from decimal import Decimal
from datetime import timedelta
import shutil
import tempfile
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from django.utils import timezone

from django.test import TestCase, Client, tag, override_settings
from django.contrib.messages import get_messages
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.urls import reverse
from unittest.mock import ANY, patch
from bs4 import BeautifulSoup
from api.services.daily_credit_settings import get_daily_credit_settings_for_plan
from constants.plans import PlanNames
from django.core.files.uploadedfile import SimpleUploadedFile
from util.onboarding import (
    TRIAL_ONBOARDING_PENDING_SESSION_KEY,
    TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY,
    TRIAL_ONBOARDING_TARGET_AGENT_UI,
    TRIAL_ONBOARDING_TARGET_SESSION_KEY,
)


@tag("batch_console_agents")
class ConsoleViewsTest(TestCase):
    def setUp(self):
        """Set up test user and client."""
        User = get_user_model()
        self.user = User.objects.create_user(
            username='test@example.com',
            email='test@example.com',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(email='test@example.com', password='testpass123')

    def _get_agent_list_payload(self, response):
        """Parse the embedded JSON payload that hydrates the React agent list."""
        soup = BeautifulSoup(response.content, 'html.parser')
        script = soup.find('script', id='persistent-agents-props')
        self.assertIsNotNone(script, "Agent list payload script tag missing")
        self.assertTrue(script.string, "Agent list payload script is empty")
        return json.loads(script.string)

    @tag("batch_console_agents")
    def test_staff_agent_audit_page_exposes_admin_settings_url(self):
        from api.models import BrowserUseAgent, PersistentAgent

        User = get_user_model()
        admin_user = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="testpass123",
        )
        self.client.force_login(admin_user)

        browser_agent = BrowserUseAgent.objects.create(
            user=admin_user,
            name="Audit Browser Agent",
        )
        persistent_agent = PersistentAgent.objects.create(
            user=admin_user,
            name="Audit Agent",
            charter="Audit charter",
            browser_use_agent=browser_agent,
        )

        response = self.client.get(reverse("console-agent-audit", kwargs={"agent_id": persistent_agent.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'data-admin-agent-url="/admin/api/persistentagent/{persistent_agent.id}/change/"',
        )

    @tag("batch_console_agents")
    def test_staff_agent_audit_page_accessible_for_soft_deleted_agent(self):
        from api.models import BrowserUseAgent, PersistentAgent

        User = get_user_model()
        admin_user = User.objects.create_superuser(
            username="admin-soft-delete@example.com",
            email="admin-soft-delete@example.com",
            password="testpass123",
        )

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Soft Delete Browser Agent",
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Soft Delete Agent",
            charter="Audit after soft delete",
            browser_use_agent=browser_agent,
        )

        delete_response = self.client.delete(reverse("agent_delete", kwargs={"pk": persistent_agent.id}))
        self.assertEqual(delete_response.status_code, 200)

        self.client.force_login(admin_user)
        response = self.client.get(reverse("console-agent-audit", kwargs={"agent_id": persistent_agent.id}))
        self.assertEqual(response.status_code, 200)

    @tag("batch_console_agents")
    def test_admin_action_can_undelete_soft_deleted_agent(self):
        from api.models import BrowserUseAgent, PersistentAgent

        User = get_user_model()
        admin_user = User.objects.create_superuser(
            username="admin-undelete@example.com",
            email="admin-undelete@example.com",
            password="testpass123",
        )

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Undelete Browser Agent",
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Undelete Agent",
            charter="Admin undelete test",
            browser_use_agent=browser_agent,
            is_deleted=True,
            deleted_at=timezone.now(),
            is_active=False,
            life_state=PersistentAgent.LifeState.EXPIRED,
            schedule=None,
        )

        self.client.force_login(admin_user)
        response = self.client.post(
            reverse("admin:api_persistentagent_changelist"),
            {
                "action": "undelete_selected_agents",
                "_selected_action": [str(persistent_agent.id)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        persistent_agent.refresh_from_db()
        self.assertFalse(persistent_agent.is_deleted)
        self.assertIsNone(persistent_agent.deleted_at)

    @tag("batch_console_agents")
    def test_admin_action_undelete_skips_agent_with_name_conflict(self):
        from api.models import BrowserUseAgent, PersistentAgent

        User = get_user_model()
        admin_user = User.objects.create_superuser(
            username="admin-undelete-conflict@example.com",
            email="admin-undelete-conflict@example.com",
            password="testpass123",
        )

        deleted_browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Conflict Deleted Browser Agent",
        )
        deleted_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Conflict Agent",
            charter="Deleted copy",
            browser_use_agent=deleted_browser_agent,
            is_deleted=True,
            deleted_at=timezone.now(),
            is_active=False,
            life_state=PersistentAgent.LifeState.EXPIRED,
            schedule=None,
        )

        active_browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Conflict Active Browser Agent",
        )
        PersistentAgent.objects.create(
            user=self.user,
            name="Conflict Agent",
            charter="Active copy",
            browser_use_agent=active_browser_agent,
        )

        self.client.force_login(admin_user)
        response = self.client.post(
            reverse("admin:api_persistentagent_changelist"),
            {
                "action": "undelete_selected_agents",
                "_selected_action": [str(deleted_agent.id)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        deleted_agent.refresh_from_db()
        self.assertTrue(deleted_agent.is_deleted)
        self.assertIsNotNone(deleted_agent.deleted_at)

    @tag("batch_console_agents")
    def test_deleted_agent_not_accessible_on_agent_detail_for_owner(self):
        from api.models import BrowserUseAgent, PersistentAgent

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Deleted Detail Browser Agent",
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Deleted Detail Agent",
            charter="Owner should no longer access detail after delete",
            browser_use_agent=browser_agent,
        )

        delete_response = self.client.delete(reverse("agent_delete", kwargs={"pk": persistent_agent.id}))
        self.assertEqual(delete_response.status_code, 200)

        detail_response = self.client.get(reverse("agent_detail", kwargs={"pk": persistent_agent.id}))
        self.assertEqual(detail_response.status_code, 404)

    @tag("batch_console_agents")
    def test_can_create_new_agent_with_same_name_after_soft_delete(self):
        from api.models import BrowserUseAgent, PersistentAgent

        original_browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Original Same Name Browser Agent",
        )
        original_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Reusable Agent Name",
            charter="Original agent",
            browser_use_agent=original_browser_agent,
        )

        delete_response = self.client.delete(reverse("agent_delete", kwargs={"pk": original_agent.id}))
        self.assertEqual(delete_response.status_code, 200)

        replacement_browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Replacement Same Name Browser Agent",
        )
        replacement_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Reusable Agent Name",
            charter="Replacement agent",
            browser_use_agent=replacement_browser_agent,
        )

        self.assertNotEqual(original_agent.id, replacement_agent.id)
        self.assertTrue(PersistentAgent.objects.filter(id=replacement_agent.id).exists())

    @tag("batch_console_agents")
    @patch("console.views.customer_has_any_individual_subscription")
    @patch("console.views.get_stripe_customer")
    @patch("console.views.get_stripe_settings")
    def test_user_plan_api_includes_trial_days(
        self,
        mock_get_stripe_settings,
        mock_get_stripe_customer,
        mock_customer_has_any_individual_subscription,
    ):
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=14,
            scale_trial_days=30,
        )
        mock_get_stripe_customer.return_value = None

        response = self.client.get(reverse("get_user_plan"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("startup_trial_days"), 14)
        self.assertEqual(payload.get("scale_trial_days"), 30)
        self.assertTrue(payload.get("trial_eligible"))
        self.assertTrue(payload.get("pricing_modal_almost_full_screen"))
        mock_customer_has_any_individual_subscription.assert_not_called()

    @tag("batch_console_agents")
    @patch("console.views.customer_has_any_individual_subscription")
    @patch("console.views.get_stripe_customer")
    @patch("console.views.get_stripe_settings")
    def test_user_plan_api_includes_pricing_modal_flag_state(
        self,
        mock_get_stripe_settings,
        mock_get_stripe_customer,
        mock_customer_has_any_individual_subscription,
    ):
        from waffle.models import Flag

        Flag.objects.update_or_create(
            name="pricing_modal_almost_full_screen",
            defaults={
                "everyone": False,
                "percent": 0,
                "superusers": False,
                "staff": False,
                "authenticated": False,
            },
        )
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=14,
            scale_trial_days=30,
        )
        mock_get_stripe_customer.return_value = None

        response = self.client.get(reverse("get_user_plan"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload.get("pricing_modal_almost_full_screen"))
        mock_customer_has_any_individual_subscription.assert_not_called()

    @tag("batch_console_agents")
    @patch("console.views.customer_has_any_individual_subscription", return_value=True)
    @patch("console.views.get_stripe_customer", return_value=SimpleNamespace(id="cus_trial_history"))
    @patch("console.views.get_stripe_settings")
    def test_user_plan_api_marks_trial_ineligible_for_prior_subscription(
        self,
        mock_get_stripe_settings,
        _mock_get_stripe_customer,
        mock_customer_has_any_individual_subscription,
    ):
        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=14,
            scale_trial_days=30,
        )

        response = self.client.get(reverse("get_user_plan"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload.get("trial_eligible"))
        mock_customer_has_any_individual_subscription.assert_called_once_with("cus_trial_history")

    @tag("batch_console_agents")
    @patch("console.views.customer_has_any_individual_subscription")
    @patch("console.views.get_stripe_customer")
    @patch("console.views.get_stripe_settings")
    def test_agent_chat_shell_exposes_trial_days_in_data_attributes(
        self,
        mock_get_stripe_settings,
        mock_get_stripe_customer,
        mock_customer_has_any_individual_subscription,
    ):
        from api.models import BrowserUseAgent, PersistentAgent

        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=9,
            scale_trial_days=18,
        )
        mock_get_stripe_customer.return_value = None

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Trial Days Browser Agent",
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Trial Days Agent",
            charter="Trial days charter",
            browser_use_agent=browser_agent,
        )

        response = self.client.get(reverse("agent_chat_shell", kwargs={"pk": persistent_agent.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-startup-trial-days="9"')
        self.assertContains(response, 'data-scale-trial-days="18"')
        self.assertContains(response, 'data-trial-eligible="true"')
        self.assertContains(response, 'data-pricing-modal-almost-full-screen="true"')
        self.assertContains(response, 'data-is-staff="false"')
        mock_customer_has_any_individual_subscription.assert_not_called()

    @tag("batch_console_agents")
    @patch("console.views.customer_has_any_individual_subscription")
    @patch("console.views.get_stripe_customer")
    @patch("console.views.get_stripe_settings")
    def test_agent_chat_shell_exposes_pricing_modal_flag_data_attribute_state(
        self,
        mock_get_stripe_settings,
        mock_get_stripe_customer,
        mock_customer_has_any_individual_subscription,
    ):
        from api.models import BrowserUseAgent, PersistentAgent
        from waffle.models import Flag

        Flag.objects.update_or_create(
            name="pricing_modal_almost_full_screen",
            defaults={
                "everyone": False,
                "percent": 0,
                "superusers": False,
                "staff": False,
                "authenticated": False,
            },
        )

        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=9,
            scale_trial_days=18,
        )
        mock_get_stripe_customer.return_value = None

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Pricing Modal Flag Browser Agent",
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Pricing Modal Flag Agent",
            charter="Pricing modal flag charter",
            browser_use_agent=browser_agent,
        )

        response = self.client.get(reverse("agent_chat_shell", kwargs={"pk": persistent_agent.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-pricing-modal-almost-full-screen="false"')
        mock_customer_has_any_individual_subscription.assert_not_called()

    @tag("batch_console_agents")
    @patch("console.views.customer_has_any_individual_subscription", return_value=True)
    @patch("console.views.get_stripe_customer", return_value=SimpleNamespace(id="cus_trial_history"))
    @patch("console.views.get_stripe_settings")
    def test_agent_chat_shell_exposes_trial_ineligible_data_attribute(
        self,
        mock_get_stripe_settings,
        _mock_get_stripe_customer,
        mock_customer_has_any_individual_subscription,
    ):
        from api.models import BrowserUseAgent, PersistentAgent

        mock_get_stripe_settings.return_value = SimpleNamespace(
            startup_trial_days=9,
            scale_trial_days=18,
        )

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Trial Eligibility Browser Agent",
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Trial Eligibility Agent",
            charter="Trial eligibility charter",
            browser_use_agent=browser_agent,
        )

        response = self.client.get(reverse("agent_chat_shell", kwargs={"pk": persistent_agent.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-trial-eligible="false"')
        mock_customer_has_any_individual_subscription.assert_called_once_with("cus_trial_history")

    @tag("batch_console_agents")
    def test_agent_chat_shell_exposes_audit_url_for_staff(self):
        from api.models import BrowserUseAgent, PersistentAgent

        User = get_user_model()
        staff_user = User.objects.create_superuser(
            username="staff@example.com",
            email="staff@example.com",
            password="testpass123",
        )
        self.client.force_login(staff_user)

        browser_agent = BrowserUseAgent.objects.create(
            user=staff_user,
            name="Staff Browser Agent",
        )
        persistent_agent = PersistentAgent.objects.create(
            user=staff_user,
            name="Staff Agent",
            charter="Audit charter",
            browser_use_agent=browser_agent,
        )

        response = self.client.get(reverse("agent_chat_shell", kwargs={"pk": persistent_agent.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-is-staff="true"')
        self.assertContains(
            response,
            f'data-audit-url="/console/staff/agents/{persistent_agent.id}/audit/"',
        )
        self.assertContains(
            response,
            'data-audit-url-template="/console/staff/agents/00000000-0000-0000-0000-000000000000/audit/"',
        )

    @tag("batch_console_agents")
    def test_delete_persistent_agent_soft_deletes_and_preserves_browser_agent(self):
        """Deleting from console should soft-delete the persistent agent and keep browser rows."""
        from api.models import PersistentAgent, BrowserUseAgent

        # Create a browser use agent
        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Test Browser Agent'
        )
        
        # Create a persistent agent linked to the browser agent
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name='Test Persistent Agent',
            charter='Test charter',
            schedule='0 12 * * *',
            browser_use_agent=browser_agent,
        )
        
        # Store IDs for verification after deletion
        browser_agent_id = browser_agent.id
        persistent_agent_id = persistent_agent.id
        
        # Verify both agents exist before deletion
        self.assertTrue(BrowserUseAgent.objects.filter(id=browser_agent_id).exists())
        self.assertTrue(PersistentAgent.objects.filter(id=persistent_agent_id).exists())
        
        # Delete the persistent agent via the console view
        url = reverse('agent_delete', kwargs={'pk': persistent_agent_id})
        response = self.client.delete(url)
        
        # Verify the response is successful
        self.assertEqual(response.status_code, 200)
        
        # Verify the persistent agent was soft-deleted and browser agent row is retained.
        persistent_agent.refresh_from_db()
        self.assertTrue(PersistentAgent.objects.filter(id=persistent_agent_id).exists())
        self.assertTrue(BrowserUseAgent.objects.filter(id=browser_agent_id).exists())
        self.assertFalse(persistent_agent.is_active)
        self.assertEqual(persistent_agent.life_state, PersistentAgent.LifeState.EXPIRED)
        self.assertIsNone(persistent_agent.schedule)
        self.assertTrue(persistent_agent.is_deleted)
        self.assertIsNotNone(persistent_agent.deleted_at)

    @tag("batch_console_agents")
    def test_delete_persistent_agent_handles_missing_browser_agent(self):
        """Deletion should succeed even if BrowserUseAgent queries fail."""
        from api.models import PersistentAgent, BrowserUseAgent

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Missing Browser Agent'
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name='Agent With Missing Browser',
            charter='Charter',
            browser_use_agent=browser_agent
        )

        empty_qs = BrowserUseAgent.objects.none()
        url = reverse('agent_delete', kwargs={'pk': persistent_agent.id})

        with patch.object(BrowserUseAgent.objects, 'filter', return_value=empty_qs):
            response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        persistent_agent.refresh_from_db()
        self.assertFalse(persistent_agent.is_active)
        self.assertEqual(persistent_agent.life_state, PersistentAgent.LifeState.EXPIRED)
        self.assertTrue(persistent_agent.is_deleted)
        self.assertIsNotNone(persistent_agent.deleted_at)

    @tag("batch_console_agents")
    def test_delete_persistent_agent_missing_browser_row(self):
        """A corrupted missing BrowserUseAgent row should not block soft deletion."""
        from django.db import connection
        from api.models import PersistentAgent, BrowserUseAgent

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Missing Browser Agent Row'
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name='Agent With Missing Browser Row',
            charter='Charter',
            browser_use_agent=browser_agent
        )

        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                cursor.execute("SET session_replication_role = replica;")
                delete_sql = "DELETE FROM api_browseruseagent WHERE id = %s"
                try:
                    cursor.execute(delete_sql, [str(browser_agent.id)])
                finally:
                    cursor.execute("SET session_replication_role = DEFAULT;")
            else:
                cursor.execute("PRAGMA foreign_keys = OFF;")
                placeholder = "?"
                delete_sql = f"DELETE FROM api_browseruseagent WHERE id = {placeholder}"
                try:
                    cursor.execute(delete_sql, [str(browser_agent.id)])
                finally:
                    cursor.execute("PRAGMA foreign_keys = ON;")

        url = reverse('agent_delete', kwargs={'pk': persistent_agent.id})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        persistent_agent.refresh_from_db()
        self.assertFalse(persistent_agent.is_active)
        self.assertEqual(persistent_agent.life_state, PersistentAgent.LifeState.EXPIRED)
        self.assertTrue(persistent_agent.is_deleted)
        self.assertIsNotNone(persistent_agent.deleted_at)

    @tag("batch_console_agents")
    def test_delete_persistent_agent_handles_delete_raises_browser_agent_missing(self):
        """Soft-delete path should not call PersistentAgent.delete()."""
        from api.models import PersistentAgent, BrowserUseAgent

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Flaky Browser Agent",
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Flaky Persistent Agent",
            charter="Charter",
            browser_use_agent=browser_agent,
        )

        url = reverse('agent_delete', kwargs={'pk': persistent_agent.id})

        with patch.object(PersistentAgent, 'delete', side_effect=BrowserUseAgent.DoesNotExist) as mock_delete:
            response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        mock_delete.assert_not_called()
        persistent_agent.refresh_from_db()
        self.assertFalse(persistent_agent.is_active)
        self.assertEqual(persistent_agent.life_state, PersistentAgent.LifeState.EXPIRED)
        self.assertTrue(persistent_agent.is_deleted)
        self.assertIsNotNone(persistent_agent.deleted_at)
        self.assertTrue(BrowserUseAgent.objects.filter(id=browser_agent.id).exists())

    @tag("batch_console_agents")
    def test_delete_persistent_agent_with_tasks(self):
        """Soft-deleting an agent with BrowserUseAgentTask rows should not error."""
        from api.models import PersistentAgent, BrowserUseAgent, BrowserUseAgentTask

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Agent With Tasks Browser'
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name='Agent With Tasks',
            charter='Task charter',
            browser_use_agent=browser_agent
        )

        BrowserUseAgentTask.objects.create(agent=browser_agent, user=self.user)

        url = reverse('agent_delete', kwargs={'pk': persistent_agent.id})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        persistent_agent.refresh_from_db()
        self.assertFalse(persistent_agent.is_active)
        self.assertEqual(persistent_agent.life_state, PersistentAgent.LifeState.EXPIRED)
        self.assertTrue(persistent_agent.is_deleted)
        self.assertIsNotNone(persistent_agent.deleted_at)
        self.assertTrue(BrowserUseAgent.objects.filter(id=browser_agent.id).exists())

    @tag("batch_console_agents")
    def test_delete_persistent_agent_invalidates_account_info_cache(self):
        from api.models import PersistentAgent, BrowserUseAgent
        from pages.account_info_cache import account_info_cache_key

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Cache Invalidating Browser Agent',
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name='Cache Invalidating Agent',
            charter='Cache test',
            browser_use_agent=browser_agent,
        )

        cache_key = account_info_cache_key(self.user.id)
        cache.set(
            cache_key,
            {
                "data": {"account": {"usage": {"agents_in_use": 1, "agents_available": 0}}},
                "refreshed_at": timezone.now().timestamp(),
            },
            timeout=600,
        )
        self.assertIsNotNone(cache.get(cache_key))

        url = reverse('agent_delete', kwargs={'pk': persistent_agent.id})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(cache.get(cache_key))

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @tag("batch_console_agents")
    def test_delete_personal_agent_trial_requirement_returns_forbidden(self):
        from api.models import PersistentAgent, BrowserUseAgent

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Trial Gated Browser Agent",
        )
        persistent_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Trial Gated Agent",
            charter="Trial gated",
            browser_use_agent=browser_agent,
        )

        response = self.client.delete(reverse("agent_delete", kwargs={"pk": persistent_agent.id}))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(PersistentAgent.objects.filter(id=persistent_agent.id).exists())
        persistent_agent.refresh_from_db()
        self.assertFalse(persistent_agent.is_deleted)
        self.assertIsNone(persistent_agent.deleted_at)
        self.assertTrue(BrowserUseAgent.objects.filter(id=browser_agent.id).exists())

    @patch("console.views.AgentService.has_agents_available", return_value=True)
    @tag("batch_console_agents")
    def test_org_agent_creation_blocked_without_seat(self, _mock_agents_available):
        """Org-owned agent creation should surface a validation error when no seats exist."""
        from api.models import Organization, OrganizationMembership, PersistentAgent

        org = Organization.objects.create(
            name="Seatless Inc",
            slug="seatless-inc",
            created_by=self.user,
        )
        OrganizationMembership.objects.create(
            org=org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        session = self.client.session
        session["agent_charter"] = "Help with tasks"
        session["context_type"] = "organization"
        session["context_id"] = str(org.id)
        session["context_name"] = org.name
        session.save()

        response = self.client.post(
            reverse("agent_create_contact"),
            data={
                "preferred_contact_method": "email",
                "contact_endpoint_email": "owner@example.com",
                "email_enabled": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.redirect_chain)
        form = response.context.get("form")
        self.assertIsNotNone(form)
        non_field_errors = form.non_field_errors()
        billing_url = f"{reverse('billing')}?org_id={org.id}"
        self.assertTrue(any("Add seats in Billing" in err for err in non_field_errors))
        context_data = response.context
        if hasattr(context_data, 'get'):
            messages_iter = context_data.get('messages')
            self.assertIsNotNone(messages_iter)
        else:
            messages_iter = None
            for ctx in context_data:
                if 'messages' in ctx:
                    messages_iter = ctx['messages']
            self.assertIsNotNone(messages_iter)
        django_messages = list(messages_iter)
        self.assertTrue(
            any(
                "Add seats in Billing" in msg.message and billing_url in msg.message
                for msg in django_messages
            )
        )
        self.assertEqual(PersistentAgent.objects.filter(organization=org).count(), 0)

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @patch("console.views.AgentService.has_agents_available", return_value=True)
    @tag("batch_console_agents")
    def test_personal_agent_creation_requires_trial(self, _mock_agents_available):
        from api.models import PersistentAgent

        session = self.client.session
        session["agent_charter"] = "Help with tasks"
        session["context_type"] = "personal"
        session["context_id"] = str(self.user.id)
        session["context_name"] = self.user.get_full_name() or self.user.username
        session.save()

        response = self.client.post(
            reverse("agent_create_contact"),
            data={
                "preferred_contact_method": "email",
                "contact_endpoint_email": "owner@example.com",
                "email_enabled": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        form = response.context.get("form")
        self.assertIsNotNone(form)
        self.assertTrue(
            any("Start a free trial" in error for error in form.non_field_errors()),
            form.non_field_errors(),
        )
        self.assertEqual(
            PersistentAgent.objects.filter(user=self.user, organization__isnull=True).count(),
            0,
        )

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @tag("batch_console_agents")
    def test_quick_spawn_trial_requirement_redirects_to_trial_onboarding_modal(self):
        session = self.client.session
        session["agent_charter"] = "Help with tasks"
        session.save()

        response = self.client.get(reverse("agent_quick_spawn"))

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, "/app/agents/new")
        query = parse_qs(parsed.query)
        self.assertEqual(query.get("spawn"), ["1"])

        session = self.client.session
        self.assertTrue(session.get(TRIAL_ONBOARDING_PENDING_SESSION_KEY))
        self.assertEqual(
            session.get(TRIAL_ONBOARDING_TARGET_SESSION_KEY),
            TRIAL_ONBOARDING_TARGET_AGENT_UI,
        )
        self.assertTrue(session.get(TRIAL_ONBOARDING_REQUIRES_PLAN_SELECTION_SESSION_KEY))

    @tag("batch_console_agents")
    @patch('api.services.agent_settings_resume.process_agent_events_task.delay')
    @patch('util.analytics.Analytics.track_event')
    def test_agent_detail_updates_daily_credit_limit(self, mock_track_event, mock_resume_delay):
        from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentStep, PersistentAgentSystemStep

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Limit Browser'
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name='Limit Test Agent',
            charter='Ensure limits',
            browser_use_agent=browser_agent
        )

        url = reverse('agent_detail', kwargs={'pk': agent.id})

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {
                'name': agent.name,
                'charter': agent.charter,
                'is_active': 'on',
                'daily_credit_limit': '2',
            })
        self.assertEqual(response.status_code, 302)
        mock_resume_delay.assert_called_once_with(str(agent.id))

        agent.refresh_from_db()
        self.assertEqual(agent.daily_credit_limit, 2)
        self.assertEqual(agent.get_daily_credit_soft_target(), Decimal('2'))
        self.assertEqual(agent.get_daily_credit_hard_limit(), Decimal('4.00'))
        latest_system_step = (
            PersistentAgentSystemStep.objects
            .filter(step__agent=agent, code=PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE)
            .select_related('step')
            .order_by('-step__created_at')
            .first()
        )
        self.assertIsNotNone(latest_system_step)
        self.assertIn("Daily credit soft target changed", latest_system_step.step.description)

        with patch('tasks.services.TaskCreditService.check_and_consume_credit_for_owner', return_value={'success': True, 'credit': None}):
            PersistentAgentStep.objects.create(
                agent=agent,
                description='Usage',
                credits_cost=Decimal('4.3'),
            )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['daily_credit_limit'], Decimal('2'))
        self.assertEqual(response.context['daily_credit_hard_limit'], Decimal('4.00'))
        self.assertEqual(response.context['daily_credit_usage'], Decimal('4.3'))
        self.assertTrue(response.context['daily_credit_low'])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {
                'name': agent.name,
                'charter': agent.charter,
                'is_active': 'on',
                'daily_credit_limit': '',
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_resume_delay.call_count, 2)

        agent.refresh_from_db()
        self.assertIsNone(agent.daily_credit_limit)
        response = self.client.get(url)
        self.assertFalse(response.context['daily_credit_low'])

    @tag("batch_console_agents")
    def test_agent_quick_settings_api_get(self):
        from api.models import PersistentAgent, BrowserUseAgent

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Daily Credits Browser',
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name='Daily Credits Agent',
            charter='Test daily credits API',
            browser_use_agent=browser_agent,
            daily_credit_limit=5,
        )

        url = reverse('console_agent_quick_settings', kwargs={'agent_id': agent.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        self.assertIn('settings', payload)
        self.assertIn('status', payload)
        self.assertIn('dailyCredits', payload['settings'])
        self.assertIn('dailyCredits', payload['status'])
        self.assertEqual(payload['settings']['dailyCredits']['limit'], 5.0)
        self.assertFalse(payload['status']['dailyCredits']['softTargetExceeded'])
        self.assertFalse(payload['status']['dailyCredits']['hardLimitReached'])

    @tag("batch_console_agents")
    @patch('api.services.agent_settings_resume.process_agent_events_task.delay')
    def test_agent_quick_settings_api_updates_limit(self, mock_resume_delay):
        from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentSystemStep

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Daily Credits Update Browser',
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name='Daily Credits Update Agent',
            charter='Update daily credits API',
            browser_use_agent=browser_agent,
        )

        url = reverse('console_agent_quick_settings', kwargs={'agent_id': agent.id})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                data=json.dumps({'dailyCredits': {'daily_credit_limit': 7}}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)

        agent.refresh_from_db()
        self.assertEqual(agent.daily_credit_limit, 7)
        mock_resume_delay.assert_called_once_with(str(agent.id))
        latest_system_step = (
            PersistentAgentSystemStep.objects
            .filter(step__agent=agent, code=PersistentAgentSystemStep.Code.SYSTEM_DIRECTIVE)
            .select_related('step')
            .order_by('-step__created_at')
            .first()
        )
        self.assertIsNotNone(latest_system_step)
        self.assertIn("Daily credit soft target changed", latest_system_step.step.description)

    @tag("batch_console_agents")
    def test_agent_quick_settings_api_hard_limit_blocked(self):
        from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentStep, PersistentAgentSystemStep

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Daily Credits Blocked Browser',
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name='Daily Credits Blocked Agent',
            charter='Blocked daily credits',
            browser_use_agent=browser_agent,
            daily_credit_limit=1,
        )

        step = PersistentAgentStep.objects.create(
            agent=agent,
            description='Blocked by daily limit',
            credits_cost=Decimal("2"),
        )
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            notes='daily_credit_limit_mid_loop',
        )

        url = reverse('console_agent_quick_settings', kwargs={'agent_id': agent.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        status = payload.get('status', {}).get('dailyCredits', {})
        self.assertTrue(status.get('hardLimitBlocked'))
        self.assertTrue(status.get('hardLimitReached'))

        response = self.client.post(
            url,
            data=json.dumps({'dailyCredits': {'daily_credit_limit': 3}}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        status = payload.get('status', {}).get('dailyCredits', {})
        self.assertFalse(status.get('hardLimitBlocked'))
        self.assertFalse(status.get('hardLimitReached'))

    @tag("batch_console_agents")
    def test_agent_addons_api_task_pack_update_queues_owner_resume(self):
        from api.models import PersistentAgent, BrowserUseAgent

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Addons API Browser",
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Addons API Agent",
            charter="Task pack resume flow",
            browser_use_agent=browser_agent,
        )

        url = reverse("console_agent_addons", kwargs={"agent_id": agent.id})
        body = {"taskPacks": {"quantities": {"price_task_pack": 1}}}

        with patch("console.api_views._can_manage_contact_packs", return_value=True), \
             patch("console.api_views.update_task_pack_quantities", return_value=(True, None, 200)) as mock_update_task, \
             patch("console.api_views.build_agent_addons_payload", return_value={"status": {}}), \
             patch("console.api_views.queue_owner_task_pack_resume", return_value=1) as mock_owner_resume, \
             patch("console.api_views.queue_settings_change_resume") as mock_agent_resume:
            response = self.client.post(
                url,
                data=json.dumps(body),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        mock_update_task.assert_called_once_with(
            owner=self.user,
            owner_type="user",
            plan_id=ANY,
            quantities={"price_task_pack": 1},
        )
        mock_owner_resume.assert_called_once_with(
            owner_id=self.user.id,
            owner_type="user",
            source="agent_addons_api_owner_resume",
        )
        mock_agent_resume.assert_not_called()

    @tag("batch_console_agents")
    def test_agent_addons_api_falls_back_to_agent_resume_when_owner_resume_noops(self):
        from api.models import PersistentAgent, BrowserUseAgent

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Addons API Fallback Browser",
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Addons API Fallback Agent",
            charter="Task pack fallback resume flow",
            browser_use_agent=browser_agent,
        )

        url = reverse("console_agent_addons", kwargs={"agent_id": agent.id})
        body = {"taskPacks": {"quantities": {"price_task_pack": 1}}}

        with patch("console.api_views._can_manage_contact_packs", return_value=True), \
             patch("console.api_views.update_task_pack_quantities", return_value=(True, None, 200)), \
             patch("console.api_views.build_agent_addons_payload", return_value={"status": {}}), \
             patch("console.api_views.queue_owner_task_pack_resume", return_value=0), \
             patch("console.api_views.queue_settings_change_resume", return_value=True) as mock_agent_resume:
            response = self.client.post(
                url,
                data=json.dumps(body),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        mock_agent_resume.assert_called_once_with(
            agent,
            task_pack_changed=True,
            source="agent_addons_api",
        )

    @tag("batch_console_agents")
    def test_agent_addons_api_reports_billing_delinquent_for_past_due_subscription(self):
        from api.models import PersistentAgent, BrowserUseAgent

        class FakeSubscriptions:
            def __init__(self, subscriptions):
                self._subscriptions = subscriptions

            def all(self):
                return list(self._subscriptions)

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Past Due Browser",
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Past Due Agent",
            charter="Billing warning",
            browser_use_agent=browser_agent,
        )
        url = reverse("console_agent_addons", kwargs={"agent_id": agent.id})

        customer = SimpleNamespace(
            subscriptions=FakeSubscriptions([
                SimpleNamespace(
                    id="sub_past_due",
                    status="past_due",
                    stripe_data={
                        "status": "past_due",
                        "current_period_end": 200,
                        "created": 100,
                        "latest_invoice": {
                            "status": "open",
                            "payment_intent": {"status": "requires_payment_method"},
                        },
                    },
                )
            ])
        )

        with patch("console.api_views._can_manage_contact_packs", return_value=True), \
             patch("console.agent_addons.get_user_plan", return_value={"id": "startup", "name": "Startup"}), \
             patch("console.agent_addons.get_active_subscription", return_value=None), \
             patch("console.agent_addons.get_stripe_customer", return_value=customer), \
             patch(
                 "console.agent_addons._build_contact_cap_payload",
                 return_value=(
                     {
                         "limit": 100,
                         "used": 0,
                         "remaining": 100,
                         "active": 0,
                         "pending": 0,
                         "unlimited": False,
                     },
                     False,
                 ),
             ), \
             patch("console.agent_addons._build_contact_pack_options", return_value=[]), \
             patch("console.agent_addons._build_task_pack_options", return_value=[]):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        billing = payload.get("status", {}).get("billing", {})
        self.assertTrue(billing.get("delinquent"))
        self.assertTrue(billing.get("actionable"))
        self.assertEqual(billing.get("reason"), "past_due")
        self.assertEqual(billing.get("subscriptionStatus"), "past_due")
        self.assertEqual(billing.get("paymentIntentStatus"), "requires_payment_method")
        self.assertEqual(billing.get("manageBillingUrl"), "/console/billing/")

    @tag("batch_console_agents")
    def test_agent_addons_api_reports_billing_not_delinquent_for_active_subscription(self):
        from api.models import PersistentAgent, BrowserUseAgent

        class FakeSubscriptions:
            def __init__(self, subscriptions):
                self._subscriptions = subscriptions

            def all(self):
                return list(self._subscriptions)

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Active Billing Browser",
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Active Billing Agent",
            charter="No billing warning",
            browser_use_agent=browser_agent,
        )
        url = reverse("console_agent_addons", kwargs={"agent_id": agent.id})

        customer = SimpleNamespace(
            subscriptions=FakeSubscriptions([
                SimpleNamespace(
                    id="sub_active",
                    status="active",
                    stripe_data={
                        "status": "active",
                        "current_period_end": 200,
                        "created": 100,
                        "latest_invoice": {
                            "status": "paid",
                            "payment_intent": {"status": "succeeded"},
                        },
                    },
                )
            ])
        )

        with patch("console.api_views._can_manage_contact_packs", return_value=True), \
             patch("console.agent_addons.get_user_plan", return_value={"id": "startup", "name": "Startup"}), \
             patch("console.agent_addons.get_active_subscription", return_value=None), \
             patch("console.agent_addons.get_stripe_customer", return_value=customer), \
             patch(
                 "console.agent_addons._build_contact_cap_payload",
                 return_value=(
                     {
                         "limit": 100,
                         "used": 0,
                         "remaining": 100,
                         "active": 0,
                         "pending": 0,
                         "unlimited": False,
                     },
                     False,
                 ),
             ), \
             patch("console.agent_addons._build_contact_pack_options", return_value=[]), \
             patch("console.agent_addons._build_task_pack_options", return_value=[]):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        billing = payload.get("status", {}).get("billing", {})
        self.assertFalse(billing.get("delinquent"))
        self.assertFalse(billing.get("actionable"))
        self.assertIsNone(billing.get("reason"))

    @tag("batch_console_agents")
    def test_agent_addons_api_reports_billing_delinquent_for_retrying_invoice(self):
        from api.models import PersistentAgent, BrowserUseAgent

        class FakeSubscriptions:
            def __init__(self, subscriptions):
                self._subscriptions = subscriptions

            def all(self):
                return list(self._subscriptions)

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name="Retrying Invoice Browser",
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Retrying Invoice Agent",
            charter="Billing retry warning",
            browser_use_agent=browser_agent,
        )
        url = reverse("console_agent_addons", kwargs={"agent_id": agent.id})

        customer = SimpleNamespace(
            subscriptions=FakeSubscriptions([
                SimpleNamespace(
                    id="sub_retrying_invoice",
                    status="active",
                    stripe_data={
                        "status": "active",
                        "current_period_end": 200,
                        "created": 100,
                        "latest_invoice": {
                            "status": "open",
                            "attempt_count": 1,
                            "next_payment_attempt": 1730000000,
                        },
                    },
                )
            ])
        )

        with patch("console.api_views._can_manage_contact_packs", return_value=True), \
             patch("console.agent_addons.get_user_plan", return_value={"id": "startup", "name": "Startup"}), \
             patch("console.agent_addons.get_active_subscription", return_value=None), \
             patch("console.agent_addons.get_stripe_customer", return_value=customer), \
             patch(
                 "console.agent_addons._build_contact_cap_payload",
                 return_value=(
                     {
                         "limit": 100,
                         "used": 0,
                         "remaining": 100,
                         "active": 0,
                         "pending": 0,
                         "unlimited": False,
                     },
                     False,
                 ),
             ), \
             patch("console.agent_addons._build_contact_pack_options", return_value=[]), \
             patch("console.agent_addons._build_task_pack_options", return_value=[]):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        billing = payload.get("status", {}).get("billing", {})
        self.assertTrue(billing.get("delinquent"))
        self.assertTrue(billing.get("actionable"))
        self.assertEqual(billing.get("reason"), "invoice_retrying")
        self.assertEqual(billing.get("subscriptionStatus"), "active")
        self.assertEqual(billing.get("latestInvoiceStatus"), "open")
        self.assertIsNone(billing.get("paymentIntentStatus"))
        self.assertEqual(billing.get("manageBillingUrl"), "/console/billing/")

    @tag("agent_credit_soft_target_batch")
    @patch('util.analytics.Analytics.track_event')
    def test_agent_detail_rejects_decimal_soft_target(self, mock_track_event):
        from api.models import PersistentAgent, BrowserUseAgent

        browser_agent = BrowserUseAgent.objects.create(user=self.user, name='Decimal Browser')
        agent = PersistentAgent.objects.create(
            user=self.user,
            name='Decimal Agent',
            charter='Precise work',
            browser_use_agent=browser_agent,
        )

        url = reverse('agent_detail', kwargs={'pk': agent.id})
        response = self.client.post(url, {
            'name': agent.name,
            'charter': agent.charter,
            'is_active': 'on',
            'daily_credit_limit': '7.75',
        }, follow=True)
        self.assertEqual(response.status_code, 200)

        agent.refresh_from_db()
        self.assertIsNone(agent.daily_credit_limit)

        self.assertFalse(mock_track_event.called)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("Enter a whole number" in message.message for message in messages),
            "Expected error message about whole number requirement",
        )

    @tag("agent_credit_soft_target_batch")
    def test_agent_detail_blank_soft_target_sets_unlimited(self):
        from api.models import PersistentAgent, BrowserUseAgent
        from console.daily_credit import get_daily_credit_slider_bounds

        browser_agent = BrowserUseAgent.objects.create(user=self.user, name='Unlimited Browser')
        agent = PersistentAgent.objects.create(
            user=self.user,
            name='Unlimited Agent',
            charter='Keep going',
            browser_use_agent=browser_agent,
            daily_credit_limit=10
        )

        url = reverse('agent_detail', kwargs={'pk': agent.id})
        response = self.client.post(url, {
            'name': agent.name,
            'charter': agent.charter,
            'is_active': 'on',
            'daily_credit_limit': '',
        })
        self.assertEqual(response.status_code, 302)
        agent.refresh_from_db()
        self.assertIsNone(agent.daily_credit_limit)

        response = self.client.get(url)
        self.assertTrue(response.context['daily_credit_unlimited'])
        credit_settings = get_daily_credit_settings_for_plan(PlanNames.FREE)
        slider_bounds = get_daily_credit_slider_bounds(credit_settings)
        self.assertEqual(response.context['daily_credit_slider_value'], slider_bounds["slider_unlimited_value"])

    @tag("agent_credit_soft_target_batch")
    def test_agent_detail_soft_target_clamps_to_bounds(self):
        from api.models import PersistentAgent, BrowserUseAgent
        from console.daily_credit import get_daily_credit_slider_bounds

        browser_agent = BrowserUseAgent.objects.create(user=self.user, name='Clamp Browser')
        agent = PersistentAgent.objects.create(
            user=self.user,
            name='Clamp Agent',
            charter='Stay bounded',
            browser_use_agent=browser_agent,
        )

        url = reverse('agent_detail', kwargs={'pk': agent.id})

        credit_settings = get_daily_credit_settings_for_plan(PlanNames.FREE)
        slider_bounds = get_daily_credit_slider_bounds(credit_settings)

        response = self.client.post(url, {
            'name': agent.name,
            'charter': agent.charter,
            'is_active': 'on',
            'daily_credit_limit': str(slider_bounds["slider_limit_max"] + Decimal('25')),
        })
        self.assertEqual(response.status_code, 302)
        agent.refresh_from_db()
        self.assertEqual(agent.daily_credit_limit, int(slider_bounds["slider_limit_max"]))

        response = self.client.post(url, {
            'name': agent.name,
            'charter': agent.charter,
            'is_active': 'on',
            'daily_credit_limit': '-5',
        })
        self.assertEqual(response.status_code, 302)
        agent.refresh_from_db()
        self.assertIsNone(agent.daily_credit_limit)

    @tag("batch_console_agents")
    def test_agent_detail_ajax_clamps_intelligence_tier_and_returns_warning(self):
        from django.db.models import Max

        from api.agent.core.llm_config import AgentLLMTier
        from config.plans import PLAN_CONFIG
        from api.models import BrowserUseAgent, IntelligenceTier, PersistentAgent

        standard = IntelligenceTier.objects.filter(key="standard").first()
        premium = IntelligenceTier.objects.filter(key="premium").first()
        max_rank = IntelligenceTier.objects.aggregate(Max("rank")).get("rank__max") or 0
        if standard is None:
            standard = IntelligenceTier.objects.create(
                key="standard",
                display_name="Standard",
                rank=max_rank + 1,
                credit_multiplier="1.00",
            )
            max_rank = standard.rank
        if premium is None:
            premium = IntelligenceTier.objects.create(
                key="premium",
                display_name="Premium",
                rank=max_rank + 1,
                credit_multiplier="1.50",
            )

        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Clamp Tier Browser")
        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Clamp Tier Agent",
            charter="Use only allowed intelligence",
            browser_use_agent=browser_agent,
            preferred_llm_tier=standard,
        )

        url = reverse("agent_detail", kwargs={"pk": agent.id})
        # Make this deterministic in CI: force proprietary mode + free plan + max tier == STANDARD.
        import console.views as console_views
        with patch.object(console_views.settings, "GOBII_PROPRIETARY_MODE", True), \
             patch("console.views.get_user_plan", return_value=PLAN_CONFIG["free"]), \
             patch("console.views.max_allowed_tier_for_plan", return_value=AgentLLMTier.STANDARD), \
             patch("util.subscription_helper.get_user_plan", return_value=PLAN_CONFIG["free"]):
            response = self.client.post(
                url,
                {
                    "name": agent.name,
                    "charter": agent.charter,
                    "is_active": "on",
                    "daily_credit_limit": "",
                    "preferred_llm_tier": premium.key,
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("preferredLlmTier"), "standard")
        self.assertTrue(payload.get("warning"))

        agent.refresh_from_db()
        self.assertEqual(getattr(agent.preferred_llm_tier, "key", None), "standard")

    @tag("batch_console_agents")
    def test_agent_detail_uploads_avatar_and_surfaces_urls(self):
        from api.models import PersistentAgent, BrowserUseAgent

        tmp_media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_media, ignore_errors=True)

        with override_settings(MEDIA_ROOT=tmp_media, MEDIA_URL='/media/'):
            browser_agent = BrowserUseAgent.objects.create(user=self.user, name='Avatar Browser')
            agent = PersistentAgent.objects.create(
                user=self.user,
                name='Avatar Agent',
                charter='Show my face',
                browser_use_agent=browser_agent,
            )

            url = reverse('agent_detail', kwargs={'pk': agent.id})
            png_bytes = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDAT\x08\xd7c\xf8\x0f"
                b"\x00\x01\x01\x01\x00\x18\xdd\x8d\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            upload = SimpleUploadedFile('avatar.png', png_bytes, content_type='image/png')

            response = self.client.post(url, {
                'name': agent.name,
                'charter': agent.charter,
                'is_active': 'on',
                'avatar': upload,
            })
            self.assertEqual(response.status_code, 302)

            agent.refresh_from_db()
            self.assertTrue(agent.avatar)
            detail_resp = self.client.get(url)
            self.assertEqual(detail_resp.status_code, 200)
            detail_props = detail_resp.context.get('agent_detail_props') or {}
            avatar_url = (detail_props.get('agent') or {}).get('avatarUrl')
            self.assertTrue(avatar_url)

            list_resp = self.client.get(reverse('agents'))
            self.assertEqual(list_resp.status_code, 200)
            list_payload = self._get_agent_list_payload(list_resp)
            first_agent = list_payload.get('agents', [])[0]
            self.assertTrue(first_agent.get('avatarUrl'))

    @tag("batch_console_agents")
    def test_agent_detail_can_clear_avatar(self):
        from api.models import PersistentAgent, BrowserUseAgent

        tmp_media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_media, ignore_errors=True)

        with override_settings(MEDIA_ROOT=tmp_media, MEDIA_URL='/media/'):
            browser_agent = BrowserUseAgent.objects.create(user=self.user, name='Avatar Browser Clear')
            agent = PersistentAgent.objects.create(
                user=self.user,
                name='Avatar Clear Agent',
                charter='Remove avatar',
                browser_use_agent=browser_agent,
            )

            png_bytes = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDAT\x08\xd7c\xf8\x0f"
                b"\x00\x01\x01\x01\x00\x18\xdd\x8d\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            agent.avatar.save('seed.png', SimpleUploadedFile('seed.png', png_bytes, content_type='image/png'), save=True)
            existing_path = agent.avatar.name
            self.assertTrue(default_storage.exists(existing_path))

            url = reverse('agent_detail', kwargs={'pk': agent.id})
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(url, {
                    'name': agent.name,
                    'charter': agent.charter,
                    'is_active': 'on',
                    'clear_avatar': 'true',
                })
            self.assertEqual(response.status_code, 302)
            self.assertGreaterEqual(len(callbacks), 1)

            agent.refresh_from_db()
            self.assertFalse(agent.avatar)
            self.assertFalse(default_storage.exists(existing_path))

            detail_resp = self.client.get(url)
            detail_props = detail_resp.context.get('agent_detail_props') or {}
            avatar_url = (detail_props.get('agent') or {}).get('avatarUrl')
            self.assertIsNone(avatar_url)

            list_resp = self.client.get(reverse('agents'))
            payload = self._get_agent_list_payload(list_resp)
            first_agent = payload.get('agents', [])[0]
            self.assertIsNone(first_agent.get('avatarUrl'))

    @tag("batch_console_agents")
    def test_agent_list_shows_daily_credit_warning(self):
        from api.models import PersistentAgent, BrowserUseAgent, PersistentAgentStep

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='List Browser'
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name='List Agent',
            charter='Monitor stuff',
            browser_use_agent=browser_agent,
            daily_credit_limit=1
        )
        last_24h_cost = Decimal('0.5')
        today_usage_cost = Decimal('1.3')
        with patch('tasks.services.TaskCreditService.check_and_consume_credit_for_owner', return_value={'success': True, 'credit': None}):
            PersistentAgentStep.objects.create(
                agent=agent,
                description='Usage',
                credits_cost=today_usage_cost,
            )
            recent_step = PersistentAgentStep.objects.create(
                agent=agent,
                description='Yesterday Usage',
                credits_cost=last_24h_cost,
            )
            now = timezone.now()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            hours_since_midnight = now - today_start
            # Shift the record to before today's reset but still within the 24h lookback window.
            delta_range = timedelta(hours=24) - hours_since_midnight
            shift = delta_range / 2 if delta_range > timedelta(0) else timedelta(minutes=1)
            shifted_timestamp = today_start - shift
            PersistentAgentStep.objects.filter(id=recent_step.id).update(
                created_at=shifted_timestamp
            )

        response = self.client.get(reverse('agents'))
        self.assertEqual(response.status_code, 200)
        payload = self._get_agent_list_payload(response)
        matching_agents = [item for item in payload['agents'] if item['name'] == 'List Agent']
        self.assertTrue(matching_agents, "Serialized payload should include the created agent")
        agent_data = matching_agents[0]
        self.assertTrue(agent_data['dailyCreditLow'])
        self.assertAlmostEqual(agent_data['dailyCreditRemaining'], 0.7, places=2)
        self.assertIn('last24hCreditBurn', agent_data)
        expected_last_24h_burn = today_usage_cost + last_24h_cost
        self.assertAlmostEqual(agent_data['last24hCreditBurn'], float(expected_last_24h_burn), places=2)

    @tag("batch_console_agents")
    def test_eval_agents_hidden_from_listing(self):
        from api.models import PersistentAgent, BrowserUseAgent

        visible_browser = BrowserUseAgent.objects.create(
            user=self.user,
            name='Visible Browser',
        )
        visible_agent = PersistentAgent.objects.create(
            user=self.user,
            name='Visible Agent',
            charter='Visible charter',
            browser_use_agent=visible_browser,
        )

        eval_browser = BrowserUseAgent.objects.create(
            user=self.user,
            name='Eval Browser',
        )
        PersistentAgent.objects.create(
            user=self.user,
            name='Eval Agent',
            charter='Eval charter',
            browser_use_agent=eval_browser,
            execution_environment='eval',
        )

        response = self.client.get(reverse('agents'))
        self.assertEqual(response.status_code, 200)
        payload = self._get_agent_list_payload(response)

        agents = payload.get('agents', [])
        names = {agent['name'] for agent in agents}

        self.assertIn(visible_agent.name, names)
        self.assertNotIn('Eval Agent', names)
        self.assertEqual(len(agents), 1)

    @tag("batch_console_agents")
    @patch('console.views.AgentService.has_agents_available', return_value=True)
    @patch('console.views.AgentService.get_agents_available', return_value=5)
    def test_agent_list_payload_includes_available_capacity(self, mock_get_available, _mock_has_available):
        response = self.client.get(reverse('agents'))
        self.assertEqual(response.status_code, 200)
        payload = self._get_agent_list_payload(response)
        self.assertIn('agentsAvailable', payload)
        self.assertEqual(payload['agentsAvailable'], 5)
        self.assertTrue(payload['canSpawnAgents'])
        mock_get_available.assert_called()

    @tag("batch_console_agents")
    def test_agent_detail_allows_selecting_dedicated_ip(self):
        from api.models import PersistentAgent, BrowserUseAgent, ProxyServer, DedicatedProxyAllocation

        browser_agent = BrowserUseAgent.objects.create(
            user=self.user,
            name='Dedicated Browser'
        )
        agent = PersistentAgent.objects.create(
            user=self.user,
            name='Dedicated Agent',
            charter='Use a dedicated proxy',
            browser_use_agent=browser_agent,
        )

        proxy = ProxyServer.objects.create(
            name='Dedicated Proxy',
            proxy_type=ProxyServer.ProxyType.HTTP,
            host='dedicated.example.com',
            port=8080,
            username='dedicated',
            password='secret',
            static_ip='198.51.100.12',
            is_active=True,
            is_dedicated=True,
        )
        DedicatedProxyAllocation.objects.assign_to_owner(proxy, self.user)

        url = reverse('agent_detail', kwargs={'pk': agent.id})

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        props = response.context.get('agent_detail_props') or {}
        dedicated_ips = props.get('dedicatedIps') or {}
        self.assertEqual(dedicated_ips.get('total'), 1)
        self.assertEqual(dedicated_ips.get('available'), 1)
        self.assertEqual(dedicated_ips.get('selectedId'), None)
        options = dedicated_ips.get('options') or []
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0], {
            'id': str(proxy.id),
            'label': '198.51.100.12',
            'inUseElsewhere': False,
            'disabled': False,
            'assignedNames': [],
        })

        response = self.client.post(url, {
            'name': agent.name,
            'charter': agent.charter,
            'is_active': 'on',
            'daily_credit_limit': '',
            'dedicated_proxy_id': str(proxy.id),
        })
        self.assertEqual(response.status_code, 302)

        browser_agent.refresh_from_db()
        self.assertEqual(browser_agent.preferred_proxy_id, proxy.id)

        response = self.client.post(url, {
            'name': agent.name,
            'charter': agent.charter,
            'is_active': 'on',
            'daily_credit_limit': '',
            'dedicated_proxy_id': '',
        })
        self.assertEqual(response.status_code, 302)

        browser_agent.refresh_from_db()
        self.assertIsNone(browser_agent.preferred_proxy_id)

    @override_settings(DEDICATED_IP_ALLOW_MULTI_ASSIGN=False)
    @tag("batch_console_agents")
    def test_agent_detail_blocks_duplicate_dedicated_ip_when_multi_assign_disabled(self):
        from api.models import PersistentAgent, BrowserUseAgent, ProxyServer, DedicatedProxyAllocation

        proxy = ProxyServer.objects.create(
            name='Dedicated Proxy Single',
            proxy_type=ProxyServer.ProxyType.HTTP,
            host='dedicated.single.example.com',
            port=8081,
            username='dedicated',
            password='secret',
            static_ip='203.0.113.25',
            is_active=True,
            is_dedicated=True,
        )
        DedicatedProxyAllocation.objects.assign_to_owner(proxy, self.user)

        browser_agent_a = BrowserUseAgent.objects.create(user=self.user, name='Agent A Browser')
        agent_a = PersistentAgent.objects.create(
            user=self.user,
            name='Agent A',
            charter='Charter A',
            browser_use_agent=browser_agent_a,
        )

        browser_agent_b = BrowserUseAgent.objects.create(user=self.user, name='Agent B Browser')
        agent_b = PersistentAgent.objects.create(
            user=self.user,
            name='Agent B',
            charter='Charter B',
            browser_use_agent=browser_agent_b,
        )

        url_a = reverse('agent_detail', kwargs={'pk': agent_a.id})
        url_b = reverse('agent_detail', kwargs={'pk': agent_b.id})

        response = self.client.post(url_a, {
            'name': agent_a.name,
            'charter': agent_a.charter,
            'is_active': 'on',
            'daily_credit_limit': '',
            'dedicated_proxy_id': str(proxy.id),
        })
        self.assertEqual(response.status_code, 302)
        browser_agent_a.refresh_from_db()
        self.assertEqual(browser_agent_a.preferred_proxy_id, proxy.id)

        response = self.client.post(url_b, {
            'name': agent_b.name,
            'charter': agent_b.charter,
            'is_active': 'on',
            'daily_credit_limit': '',
            'dedicated_proxy_id': str(proxy.id),
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        browser_agent_b.refresh_from_db()
        self.assertIsNone(browser_agent_b.preferred_proxy_id)
        self.assertContains(response, "already assigned to another agent")
