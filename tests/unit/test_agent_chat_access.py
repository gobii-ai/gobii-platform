from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from api.models import (
    AgentCollaborator,
    BrowserUseAgent,
    BrowserUseAgentTask,
    CommsChannel,
    Organization,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    UserFlags,
    UserPreference,
)
from api.agent.core.processing_flags import clear_processing_queued_flag, set_processing_queued_flag
from api.services.signup_preview import (
    resume_signup_preview_agent_if_eligible,
    resume_signup_preview_agents_for_user_if_eligible,
)
from console.agent_chat.access import resolve_agent
from util.trial_enforcement import can_user_access_personal_agent_chat


@tag("batch_console_agents")
class AgentChatAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="pw",
        )
        self.client = Client()
        self.client.login(email="owner@example.com", password="pw")

        self.org = Organization.objects.create(
            name="Acme",
            slug="acme",
            plan="free",
            created_by=self.user,
        )
        billing = self.org.billing
        billing.purchased_seats = 2
        billing.save(update_fields=["purchased_seats"])
        OrganizationMembership.objects.create(
            org=self.org,
            user=self.user,
            role=OrganizationMembership.OrgRole.OWNER,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        self.personal_agent = self._create_agent("Personal Agent", organization=None)
        self.org_agent = self._create_agent("Org Agent One", organization=self.org)
        self.org_agent_two = self._create_agent("Org Agent Two", organization=self.org)
        self._set_personal_context()

    def _create_agent(self, name, organization):
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name=name)
        return PersistentAgent.objects.create(
            user=self.user,
            organization=organization,
            name=name,
            charter="",
            browser_use_agent=browser_agent,
        )

    def _seed_signup_preview_followup_messages(self, agent: PersistentAgent):
        agent_endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.WEB,
            address=f"web://agent/{agent.id}",
        )
        user_endpoint = PersistentAgentCommsEndpoint.objects.create(
            channel=CommsChannel.WEB,
            address=f"web://user/{self.user.id}/agent/{agent.id}",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            is_outbound=False,
            body="Hello!",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=agent_endpoint,
            to_endpoint=user_endpoint,
            is_outbound=True,
            body="Preview reply",
        )
        PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=user_endpoint,
            to_endpoint=agent_endpoint,
            is_outbound=False,
            body="just chat",
        )

    def _set_personal_context(self):
        session = self.client.session
        session["context_type"] = "personal"
        session["context_id"] = str(self.user.id)
        session["context_name"] = self.user.get_full_name() or self.user.username
        session.save()

    def _set_org_context(self):
        session = self.client.session
        session["context_type"] = "organization"
        session["context_id"] = str(self.org.id)
        session["context_name"] = self.org.name
        session.save()

    def _fake_subscription(
        self,
        status: str,
        *,
        current_period_end: int | None = None,
        created: int | None = None,
        subscription_id: str | None = None,
    ):
        current_period_end = 0 if current_period_end is None else current_period_end
        created = 0 if created is None else created
        return SimpleNamespace(
            id=subscription_id or f"sub_{status}",
            status=status,
            stripe_data={
                "status": status,
                "current_period_end": current_period_end,
                "created": created,
            },
        )

    def _fake_customer_with_subscriptions(self, subscriptions):
        class FakeSubscriptions:
            def __init__(self, subscriptions):
                self._subscriptions = subscriptions

            def all(self):
                return list(self._subscriptions)

        return SimpleNamespace(
            subscriptions=FakeSubscriptions(
                list(subscriptions)
            )
        )

    def _fake_customer_with_subscription_status(self, status: str):
        return self._fake_customer_with_subscriptions([self._fake_subscription(status)])

    def test_resolve_agent_allows_org_agent_with_override(self):
        override = {"type": "organization", "id": str(self.org.id)}
        agent = resolve_agent(
            self.user,
            self.client.session,
            str(self.org_agent.id),
            context_override=override,
        )
        self.assertEqual(agent.id, self.org_agent.id)

    def test_resolve_agent_allows_org_agent_outside_current_personal_context(self):
        self._set_personal_context()

        agent = resolve_agent(
            self.user,
            self.client.session,
            str(self.org_agent.id),
        )

        self.assertEqual(agent.id, self.org_agent.id)

    def test_resolve_agent_allows_personal_agent_outside_current_org_context(self):
        self._set_org_context()

        agent = resolve_agent(
            self.user,
            self.client.session,
            str(self.personal_agent.id),
        )

        self.assertEqual(agent.id, self.personal_agent.id)

    def test_resolve_agent_denies_org_agent_without_membership(self):
        User = get_user_model()
        stranger = User.objects.create_user(
            username="stranger@example.com",
            email="stranger@example.com",
            password="pw",
        )
        with self.assertRaises(PermissionDenied):
            resolve_agent(stranger, {}, str(self.org_agent.id))

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_resolve_agent_denies_personal_owner_without_trial(self):
        with self.assertRaises(PermissionDenied) as raised:
            resolve_agent(self.user, self.client.session, str(self.personal_agent.id))
        self.assertIn("Start a free trial", str(raised.exception))

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @patch("util.trial_enforcement.get_active_subscription", return_value=None)
    def test_chat_shell_allows_personal_owner_with_past_due_subscription(self, _mock_get_active_subscription):
        customer = self._fake_customer_with_subscription_status("past_due")
        with patch("util.trial_enforcement.get_stripe_customer", return_value=customer):
            response = self.client.get(
                reverse("agent_chat_shell", kwargs={"pk": self.personal_agent.id})
            )

        self.assertEqual(response.status_code, 200)

    @patch("api.services.signup_preview.can_user_use_personal_agents_and_api", return_value=True)
    @patch("console.agent_chat.access.can_user_access_personal_agent_chat", return_value=True)
    def test_chat_shell_resumes_signup_preview_agent_after_plan_completion(
        self,
        _mock_personal_chat_access,
        _mock_personal_access,
    ):
        self.personal_agent.signup_preview_state = (
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
        )
        self.personal_agent.save(update_fields=["signup_preview_state", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(
                reverse("agent_chat_shell", kwargs={"pk": self.personal_agent.id})
            )

        self.assertEqual(response.status_code, 200)
        self.personal_agent.refresh_from_db()
        self.assertEqual(
            self.personal_agent.signup_preview_state,
            PersistentAgent.SignupPreviewState.NONE,
        )

    @patch("console.views.process_agent_events_task.delay")
    @patch("api.services.signup_preview.can_user_use_personal_agents_and_api", return_value=True)
    @patch("console.agent_chat.access.can_user_access_personal_agent_chat", return_value=True)
    def test_chat_shell_requeues_when_followup_message_exists_after_preview_reply(
        self,
        _mock_personal_chat_access,
        _mock_personal_access,
        mock_process_delay,
    ):
        self.personal_agent.signup_preview_state = (
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
        )
        self.personal_agent.save(update_fields=["signup_preview_state", "updated_at"])
        self._seed_signup_preview_followup_messages(self.personal_agent)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(
                reverse("agent_chat_shell", kwargs={"pk": self.personal_agent.id})
            )

        self.assertEqual(response.status_code, 200)
        self.personal_agent.refresh_from_db()
        self.assertEqual(
            self.personal_agent.signup_preview_state,
            PersistentAgent.SignupPreviewState.NONE,
        )
        mock_process_delay.assert_called_once_with(str(self.personal_agent.id))

    @patch("api.services.signup_preview.reconcile_user_plan_from_stripe", return_value={"id": "startup"})
    def test_resume_signup_preview_agent_ignores_stale_cached_personal_access(
        self,
        mock_reconcile_user_plan,
    ):
        self.personal_agent.signup_preview_state = (
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
        )
        self.personal_agent.save(update_fields=["signup_preview_state", "updated_at"])
        self.user._personal_agents_and_api_access_allowed = False

        def _can_use(user):
            cached = getattr(user, "_personal_agents_and_api_access_allowed", None)
            if cached is not None:
                return bool(cached)
            return True

        with patch(
            "api.services.signup_preview.can_user_use_personal_agents_and_api",
            side_effect=_can_use,
        ):
            resumed = resume_signup_preview_agent_if_eligible(self.personal_agent, self.user)

        self.assertTrue(resumed)
        mock_reconcile_user_plan.assert_called_once_with(self.user)
        self.personal_agent.refresh_from_db()
        self.assertEqual(
            self.personal_agent.signup_preview_state,
            PersistentAgent.SignupPreviewState.NONE,
        )

    @patch("api.services.signup_preview.reconcile_user_plan_from_stripe", return_value={"id": "startup"})
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.services.signup_preview.can_user_use_personal_agents_and_api", return_value=True)
    def test_resume_signup_preview_agents_for_user_clears_waiting_agents_without_requeue(
        self,
        _mock_personal_access,
        mock_process_delay,
        _mock_reconcile_user_plan,
    ):
        self.personal_agent.signup_preview_state = (
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
        )
        self.personal_agent.save(update_fields=["signup_preview_state", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            resumed_ids = resume_signup_preview_agents_for_user_if_eligible(self.user)

        self.assertEqual(resumed_ids, [str(self.personal_agent.id)])
        self.personal_agent.refresh_from_db()
        self.assertEqual(
            self.personal_agent.signup_preview_state,
            PersistentAgent.SignupPreviewState.NONE,
        )
        mock_process_delay.assert_not_called()

    @patch("api.services.signup_preview.reconcile_user_plan_from_stripe", return_value={"id": "startup"})
    @patch("api.agent.tasks.process_agent_events_task.delay")
    @patch("api.services.signup_preview.can_user_use_personal_agents_and_api", return_value=True)
    def test_resume_signup_preview_agents_for_user_requeues_followup_agents(
        self,
        _mock_personal_access,
        mock_process_delay,
        _mock_reconcile_user_plan,
    ):
        self.personal_agent.signup_preview_state = (
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
        )
        self.personal_agent.save(update_fields=["signup_preview_state", "updated_at"])
        self._seed_signup_preview_followup_messages(self.personal_agent)

        with self.captureOnCommitCallbacks(execute=True):
            resumed_ids = resume_signup_preview_agents_for_user_if_eligible(self.user)

        self.assertEqual(resumed_ids, [str(self.personal_agent.id)])
        self.personal_agent.refresh_from_db()
        self.assertEqual(
            self.personal_agent.signup_preview_state,
            PersistentAgent.SignupPreviewState.NONE,
        )
        mock_process_delay.assert_called_once_with(str(self.personal_agent.id))

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @patch("util.trial_enforcement.get_active_subscription", return_value=None)
    def test_resolve_agent_allows_personal_owner_with_past_due_subscription_outside_current_context(
        self,
        _mock_get_active_subscription,
    ):
        self._set_org_context()
        customer = self._fake_customer_with_subscription_status("past_due")

        with patch("util.trial_enforcement.get_stripe_customer", return_value=customer):
            agent = resolve_agent(
                self.user,
                self.client.session,
                str(self.personal_agent.id),
                allow_delinquent_personal_chat=True,
            )

        self.assertEqual(agent.id, self.personal_agent.id)

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @patch("util.trial_enforcement.get_active_subscription", return_value=None)
    def test_roster_includes_personal_agent_with_past_due_subscription(self, _mock_get_active_subscription):
        customer = self._fake_customer_with_subscription_status("past_due")
        with patch("util.trial_enforcement.get_stripe_customer", return_value=customer), \
             patch("console.agent_addons.get_stripe_customer", return_value=customer):
            response = self.client.get(reverse("console_agent_roster"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        roster_ids = {entry["id"] for entry in payload.get("agents", [])}
        self.assertIn(str(self.personal_agent.id), roster_ids)
        billing_status = payload.get("billingStatus", {})
        self.assertTrue(billing_status.get("delinquent"))
        self.assertTrue(billing_status.get("actionable"))
        self.assertEqual(billing_status.get("reason"), "past_due")

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_roster_ignores_billing_delinquency_for_grandfathered_personal_user(self):
        UserFlags.objects.create(user=self.user, is_freemium_grandfathered=True)
        customer = self._fake_customer_with_subscription_status("past_due")
        with patch("console.agent_addons.get_stripe_customer", return_value=customer):
            response = self.client.get(reverse("console_agent_roster"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        billing_status = payload.get("billingStatus", {})
        self.assertFalse(billing_status.get("delinquent"))
        self.assertFalse(billing_status.get("actionable"))
        self.assertIsNone(billing_status.get("reason"))

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_resolve_agent_allows_active_signup_preview_agent_without_plan(self):
        self.personal_agent.signup_preview_state = (
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
        )
        self.personal_agent.save(update_fields=["signup_preview_state", "updated_at"])

        agent = resolve_agent(
            self.user,
            self.client.session,
            str(self.personal_agent.id),
            allow_delinquent_personal_chat=True,
        )

        self.assertEqual(agent.id, self.personal_agent.id)

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_roster_includes_active_signup_preview_agent_without_plan(self):
        self.personal_agent.signup_preview_state = (
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
        )
        self.personal_agent.save(update_fields=["signup_preview_state", "updated_at"])

        response = self.client.get(reverse("console_agent_roster"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        roster_ids = {entry["id"] for entry in payload.get("agents", [])}
        self.assertIn(str(self.personal_agent.id), roster_ids)

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_timeline_allows_active_signup_preview_agent_without_plan(self):
        self.personal_agent.signup_preview_state = (
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
        )
        self.personal_agent.save(update_fields=["signup_preview_state", "updated_at"])

        response = self.client.get(
            reverse("console_agent_timeline", kwargs={"agent_id": self.personal_agent.id})
        )

        self.assertEqual(response.status_code, 200)

    @patch("api.services.signup_preview.can_user_use_personal_agents_and_api", return_value=True)
    def test_timeline_resumes_signup_preview_agent_after_plan_completion(
        self,
        _mock_personal_access,
    ):
        self.personal_agent.signup_preview_state = (
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
        )
        self.personal_agent.save(update_fields=["signup_preview_state", "updated_at"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(
                reverse("console_agent_timeline", kwargs={"agent_id": self.personal_agent.id})
            )

        self.assertEqual(response.status_code, 200)
        self.personal_agent.refresh_from_db()
        self.assertEqual(
            self.personal_agent.signup_preview_state,
            PersistentAgent.SignupPreviewState.NONE,
        )

    @patch("console.api_views.process_agent_events_task.delay")
    @patch("api.services.signup_preview.can_user_use_personal_agents_and_api", return_value=True)
    def test_timeline_requeues_when_followup_message_exists_after_preview_reply(
        self,
        _mock_personal_access,
        mock_process_delay,
    ):
        self.personal_agent.signup_preview_state = (
            PersistentAgent.SignupPreviewState.AWAITING_SIGNUP_COMPLETION
        )
        self.personal_agent.save(update_fields=["signup_preview_state", "updated_at"])
        self._seed_signup_preview_followup_messages(self.personal_agent)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(
                reverse("console_agent_timeline", kwargs={"agent_id": self.personal_agent.id})
            )

        self.assertEqual(response.status_code, 200)
        self.personal_agent.refresh_from_db()
        self.assertEqual(
            self.personal_agent.signup_preview_state,
            PersistentAgent.SignupPreviewState.NONE,
        )
        mock_process_delay.assert_called_once_with(str(self.personal_agent.id))

    @patch("util.trial_enforcement.can_user_use_personal_agents_and_api", return_value=False)
    def test_chat_access_ignores_historical_past_due_subscription(self, _mock_normal_access):
        customer = self._fake_customer_with_subscriptions([
            self._fake_subscription("past_due", current_period_end=100, created=100),
            self._fake_subscription("canceled", current_period_end=200, created=200),
        ])

        with patch("util.trial_enforcement.get_stripe_customer", return_value=customer):
            self.assertFalse(can_user_access_personal_agent_chat(self.user))

    @patch("util.trial_enforcement.can_user_use_personal_agents_and_api", return_value=False)
    def test_chat_access_allows_current_past_due_subscription(self, _mock_normal_access):
        customer = self._fake_customer_with_subscriptions([
            self._fake_subscription("canceled", current_period_end=100, created=100),
            self._fake_subscription("past_due", current_period_end=200, created=200),
        ])

        with patch("util.trial_enforcement.get_stripe_customer", return_value=customer):
            self.assertTrue(can_user_access_personal_agent_chat(self.user))

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    @patch("console.insight_views._get_time_saved_insight", return_value=None)
    @patch("console.insight_views._get_burn_rate_insight", return_value=None)
    @patch("util.trial_enforcement.get_active_subscription", return_value=None)
    def test_insights_omit_setup_cards_for_past_due_personal_chat(
        self,
        _mock_get_active_subscription,
        _mock_burn_rate,
        _mock_time_saved,
    ):
        customer = self._fake_customer_with_subscription_status("past_due")
        with patch("util.trial_enforcement.get_stripe_customer", return_value=customer):
            response = self.client.get(
                reverse("console_agent_insights", kwargs={"agent_id": self.personal_agent.id})
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("insights"), [])

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_resolve_agent_allows_shared_personal_agent_for_collaborator(self):
        User = get_user_model()
        collaborator = User.objects.create_user(
            username="collab@example.com",
            email="collab@example.com",
            password="pw",
        )
        with patch("util.subscription_helper.get_user_max_contacts_per_agent", return_value=0):
            AgentCollaborator.objects.create(
                agent=self.personal_agent,
                user=collaborator,
                invited_by=self.user,
            )

        agent = resolve_agent(
            collaborator,
            {},
            str(self.personal_agent.id),
            allow_shared=True,
        )
        self.assertEqual(agent.id, self.personal_agent.id)

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_resolve_agent_denies_shared_personal_agent_without_allow_shared(self):
        User = get_user_model()
        collaborator = User.objects.create_user(
            username="collab-denied@example.com",
            email="collab-denied@example.com",
            password="pw",
        )
        with patch("util.subscription_helper.get_user_max_contacts_per_agent", return_value=0):
            AgentCollaborator.objects.create(
                agent=self.personal_agent,
                user=collaborator,
                invited_by=self.user,
            )

        with self.assertRaises(PermissionDenied) as raised:
            resolve_agent(
                collaborator,
                {},
                str(self.personal_agent.id),
            )

        self.assertEqual(str(raised.exception), "Not permitted to access this agent.")

    def test_roster_uses_org_agents_for_active_org_agent(self):
        expected_last_interaction = timezone.now().replace(microsecond=0)
        self.org_agent.last_interaction_at = expected_last_interaction
        self.org_agent.save(update_fields=["last_interaction_at"])

        url = reverse("console_agent_roster")
        response = self.client.get(
            url,
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID=str(self.org.id),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("agent_roster_sort_mode"), "recent")
        self.assertEqual(payload.get("favorite_agent_ids"), [])
        roster_ids = {entry["id"] for entry in payload.get("agents", [])}
        self.assertIn(str(self.org_agent.id), roster_ids)
        self.assertIn(str(self.org_agent_two.id), roster_ids)
        self.assertNotIn(str(self.personal_agent.id), roster_ids)
        matching_entry = next(
            entry for entry in payload.get("agents", []) if entry.get("id") == str(self.org_agent.id)
        )
        self.assertEqual(matching_entry.get("last_interaction_at"), expected_last_interaction.isoformat())

    def test_roster_includes_favorite_agent_ids(self):
        UserPreference.update_known_preferences(
            self.user,
            {
                UserPreference.KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS: [
                    str(self.org_agent.id),
                ],
            },
        )

        response = self.client.get(
            reverse("console_agent_roster"),
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID=str(self.org.id),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("favorite_agent_ids"), [str(self.org_agent.id)])

    def test_roster_includes_insights_panel_expanded_preference(self):
        UserPreference.update_known_preferences(
            self.user,
            {
                UserPreference.KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED: False,
            },
        )

        response = self.client.get(
            reverse("console_agent_roster"),
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID=str(self.org.id),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload.get("insights_panel_expanded"))

    def test_roster_includes_mini_and_short_descriptions(self):
        self.org_agent.mini_description = "Revenue pipeline assistant"
        self.org_agent.short_description = "Qualifies inbound leads and drafts handoff-ready summaries."
        self.org_agent.save(update_fields=["mini_description", "short_description"])

        response = self.client.get(
            reverse("console_agent_roster"),
            HTTP_X_GOBII_CONTEXT_TYPE="organization",
            HTTP_X_GOBII_CONTEXT_ID=str(self.org.id),
        )
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        matching_entry = next(
            entry for entry in payload.get("agents", []) if entry.get("id") == str(self.org_agent.id)
        )
        self.assertEqual(matching_entry.get("mini_description"), "Revenue pipeline assistant")
        self.assertEqual(
            matching_entry.get("short_description"),
            "Qualifies inbound leads and drafts handoff-ready summaries.",
        )

    def test_roster_includes_processing_activity_for_mixed_agents(self):
        queued_agent_id = str(self.org_agent.id)
        idle_org_agent = self._create_agent("Org Agent Idle", organization=self.org)
        set_processing_queued_flag(self.org_agent.id)
        with (
            patch("api.models.BrowserUseAgentTask.full_clean", return_value=None),
            patch(
                "api.models.TaskCreditService.check_and_consume_credit_for_owner",
                return_value={"success": True, "credit": None},
            ),
        ):
            BrowserUseAgentTask.objects.create(
                agent=self.org_agent_two.browser_use_agent,
                user=self.user,
                prompt="Review the pipeline",
                status=BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
            )

        try:
            response = self.client.get(
                reverse("console_agent_roster"),
                HTTP_X_GOBII_CONTEXT_TYPE="organization",
                HTTP_X_GOBII_CONTEXT_ID=str(self.org.id),
            )
            self.assertEqual(response.status_code, 200)

            payload = response.json()
            roster_by_id = {entry["id"]: entry for entry in payload.get("agents", [])}
            self.assertTrue(roster_by_id[queued_agent_id]["processing_active"])
            self.assertTrue(roster_by_id[str(self.org_agent_two.id)]["processing_active"])
            self.assertFalse(roster_by_id[str(idle_org_agent.id)]["processing_active"])
        finally:
            clear_processing_queued_flag(self.org_agent.id)

    def test_roster_includes_audit_url_for_staff(self):
        User = get_user_model()
        staff_user = User.objects.create_superuser(
            username="staff@example.com",
            email="staff@example.com",
            password="pw",
        )
        staff_client = Client()
        staff_client.login(email="staff@example.com", password="pw")

        browser_agent = BrowserUseAgent.objects.create(user=staff_user, name="Staff Agent")
        persistent_agent = PersistentAgent.objects.create(
            user=staff_user,
            name="Staff Agent",
            charter="",
            browser_use_agent=browser_agent,
        )

        response = staff_client.get(
            reverse("console_agent_roster"),
            HTTP_X_GOBII_CONTEXT_TYPE="personal",
            HTTP_X_GOBII_CONTEXT_ID=str(staff_user.id),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        matching_entry = next(
            entry for entry in payload.get("agents", []) if entry.get("id") == str(persistent_agent.id)
        )
        self.assertEqual(
            matching_entry.get("audit_url"),
            f"/console/staff/agents/{persistent_agent.id}/audit/",
        )
