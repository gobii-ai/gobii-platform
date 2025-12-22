import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from api.models import BrowserConfig, Organization
from api.services.browser_settings import get_browser_settings_for_owner, invalidate_browser_settings_cache
from api.tasks.browser_agent_tasks import _execute_agent_with_failover, _normalize_vision_detail_level
from constants.plans import PlanNames


@tag("batch_browser_config")
class BrowserConfigSettingsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="browser-config-user@example.com",
            email="browser-config-user@example.com",
        )
        self.org = Organization.objects.create(
            name="Config Org",
            slug=f"config-org-{uuid.uuid4().hex[:6]}",
            created_by=self.user,
        )
        self.org.billing.subscription = PlanNames.ORG_TEAM
        self.org.billing.save()

        free_config, _ = BrowserConfig.objects.get_or_create(plan_name=PlanNames.FREE)
        free_config.max_browser_tasks = 5
        free_config.max_active_browser_tasks = 4
        free_config.max_browser_steps = 50
        free_config.vision_detail_level = "low"
        free_config.save()

        org_config, _ = BrowserConfig.objects.get_or_create(plan_name=PlanNames.ORG_TEAM)
        org_config.max_browser_tasks = 9
        org_config.max_active_browser_tasks = 8
        org_config.max_browser_steps = 75
        org_config.vision_detail_level = "high"
        org_config.save()

        invalidate_browser_settings_cache()

    def test_owner_specific_plan_settings(self):
        user_settings = get_browser_settings_for_owner(self.user)
        org_settings = get_browser_settings_for_owner(self.org)

        self.assertEqual(user_settings.max_browser_tasks, 5)
        self.assertEqual(user_settings.max_active_browser_tasks, 4)
        self.assertEqual(user_settings.max_browser_steps, 50)
        self.assertEqual(user_settings.vision_detail_level, "low")
        self.assertEqual(org_settings.max_browser_tasks, 9)
        self.assertEqual(org_settings.max_active_browser_tasks, 8)
        self.assertEqual(org_settings.max_browser_steps, 75)
        self.assertEqual(org_settings.vision_detail_level, "high")

    @patch("api.tasks.browser_agent_tasks._run_agent", new_callable=AsyncMock)
    def test_execute_agent_with_failover_passes_step_limit(self, mock_run_agent):
        mock_run_agent.return_value = ("ok", {})
        provider_priority = [[{
            "endpoint_key": "demo",
            "provider_key": "demo",
            "weight": 1.0,
            "browser_model": None,
            "base_url": "",
            "backend": None,
            "supports_vision": True,
            "max_output_tokens": None,
            "api_key": "sk-test",
        }]]

        result, usage = _execute_agent_with_failover(
            task_input="run",
            task_id="task-123",
            provider_priority=provider_priority,
            max_steps=42,
            vision_detail_level="high",
        )

        self.assertEqual(result, "ok")
        self.assertIsInstance(usage, dict)
        mock_run_agent.assert_called_once()
        self.assertEqual(mock_run_agent.call_args.kwargs.get("max_steps_override"), 42)
        self.assertEqual(mock_run_agent.call_args.kwargs.get("vision_detail_level"), "high")

    def test_normalize_vision_detail_level(self):
        self.assertEqual(_normalize_vision_detail_level("HIGH", True), "high")
        self.assertIsNone(_normalize_vision_detail_level("unsupported", True))
        self.assertIsNone(_normalize_vision_detail_level("low", False))

    def test_browser_task_limit_addon_applies_daily_uplift(self):
        UserBilling = apps.get_model("api", "UserBilling")
        UserBilling.objects.update_or_create(
            user=self.user,
            defaults={"subscription": PlanNames.STARTUP},
        )

        startup_config, _ = BrowserConfig.objects.get_or_create(plan_name=PlanNames.STARTUP)
        startup_config.max_browser_tasks = 10
        startup_config.max_active_browser_tasks = 3
        startup_config.max_browser_steps = 50
        startup_config.save()

        AddonEntitlement = apps.get_model("api", "AddonEntitlement")
        AddonEntitlement.objects.create(
            user=self.user,
            price_id="price_browser_limit",
            quantity=2,
            browser_task_daily_delta=5,
            starts_at=timezone.now() - timedelta(days=1),
            expires_at=timezone.now() + timedelta(days=10),
            is_recurring=True,
        )

        invalidate_browser_settings_cache()
        settings = get_browser_settings_for_owner(self.user)

        self.assertEqual(settings.max_browser_tasks, 20)
