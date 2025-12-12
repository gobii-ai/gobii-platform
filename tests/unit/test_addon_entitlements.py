from datetime import timedelta
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone
from unittest.mock import patch

from billing.addons import AddonEntitlementService
from constants.plans import PlanNames
from util.subscription_helper import get_user_max_contacts_per_agent


@tag("batch_billing")
class AddonEntitlementSyncTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="sync-user", email="sync@example.com")
        self.period_start = timezone.now()
        self.period_end = self.period_start + timedelta(days=30)

    @patch("billing.addons.get_stripe_settings")
    def test_sync_creates_entitlements_and_task_credits(self, mock_settings):
        mock_settings.return_value = SimpleNamespace(
            startup_task_pack_price_id="price_task",
            startup_contact_cap_price_id="price_contact",
            scale_task_pack_price_id="",
            scale_contact_cap_price_id="",
            org_team_task_pack_price_id="",
            org_team_contact_cap_price_id="",
            task_pack_delta_startup=250,
            task_pack_delta_scale=0,
            task_pack_delta_org_team=0,
            contact_pack_delta_startup=0,
            contact_pack_delta_scale=0,
            contact_pack_delta_org_team=0,
        )

        items = [
            {
                "price": {
                    "id": "price_task",
                    "product": "prod_task",
                    "metadata": {"task_credits_delta": "250"},
                },
                "quantity": 2,
            },
            {
                "price": {
                    "id": "price_contact",
                    "product": "prod_contact",
                    "metadata": {"contact_cap_delta": "5"},
                },
                "quantity": 1,
            },
        ]

        AddonEntitlementService.sync_subscription_entitlements(
            owner=self.user,
            owner_type="user",
            plan_id=PlanNames.STARTUP,
            subscription_items=items,
            period_start=self.period_start,
            period_end=self.period_end,
            created_via="test_sync",
        )

        entitlements = AddonEntitlementService.get_active_entitlements(self.user)
        self.assertEqual(entitlements.count(), 2)

        task_entitlement = entitlements.get(price_id="price_task")
        self.assertEqual(task_entitlement.quantity, 2)
        self.assertEqual(task_entitlement.task_credits_delta, 250)
        self.assertEqual(task_entitlement.expires_at, self.period_end)

        contact_entitlement = entitlements.get(price_id="price_contact")
        self.assertEqual(contact_entitlement.contact_cap_delta, 5)
        self.assertEqual(contact_entitlement.quantity, 1)

        TaskCredit = apps.get_model("api", "TaskCredit")
        addon_blocks = TaskCredit.objects.filter(
            user=self.user, stripe_invoice_id__startswith="addon:price_task"
        )
        self.assertEqual(addon_blocks.count(), 1)
        self.assertEqual(int(addon_blocks.first().credits), 500)
        self.assertEqual(addon_blocks.first().grant_type, "task_pack")

        # Remove contact pack and ensure it expires
        AddonEntitlementService.sync_subscription_entitlements(
            owner=self.user,
            owner_type="user",
            plan_id=PlanNames.STARTUP,
            subscription_items=[items[0]],
            period_start=self.period_start,
            period_end=self.period_end,
            created_via="test_sync",
        )
        contact_entitlement.refresh_from_db()
        self.assertLessEqual(contact_entitlement.expires_at, timezone.now())

    @patch("billing.addons.get_stripe_settings")
    def test_contact_pack_delta_overridden_per_plan(self, mock_settings):
        mock_settings.return_value = SimpleNamespace(
            startup_task_pack_price_id="",
            startup_contact_cap_price_id="price_contact",
            scale_task_pack_price_id="",
            scale_contact_cap_price_id="",
            org_team_task_pack_price_id="",
            org_team_contact_cap_price_id="",
            task_pack_delta_startup=0,
            task_pack_delta_scale=0,
            task_pack_delta_org_team=0,
            contact_pack_delta_startup=9,
            contact_pack_delta_scale=0,
            contact_pack_delta_org_team=0,
        )

        items = [
            {
                "price": {
                    "id": "price_contact",
                    "product": "prod_contact",
                    "metadata": {},  # no contact_cap_delta; rely on per-plan override
                },
                "quantity": 2,
            },
        ]

        AddonEntitlementService.sync_subscription_entitlements(
            owner=self.user,
            owner_type="user",
            plan_id=PlanNames.STARTUP,
            subscription_items=items,
            period_start=self.period_start,
            period_end=self.period_end,
            created_via="test_sync",
        )

        ent = AddonEntitlementService.get_active_entitlements(self.user, "price_contact").first()
        self.assertIsNotNone(ent)
        self.assertEqual(ent.contact_cap_delta, 9)
        self.assertEqual(ent.quantity, 2)


@tag("batch_billing")
class AddonContactCapTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="cap-user", email="cap@example.com")

    def test_contact_cap_addon_applies_on_billing_override(self):
        AddonEntitlement = apps.get_model("api", "AddonEntitlement")
        AddonEntitlement.objects.create(
            user=self.user,
            price_id="price_contact",
            quantity=1,
            contact_cap_delta=7,
            starts_at=timezone.now() - timedelta(days=1),
            expires_at=timezone.now() + timedelta(days=10),
            is_recurring=True,
        )

        UserBilling = apps.get_model("api", "UserBilling")
        billing, _ = UserBilling.objects.get_or_create(user=self.user, defaults={"max_contacts_per_agent": 10})
        billing.max_contacts_per_agent = 10
        billing.save(update_fields=["max_contacts_per_agent"])

        self.assertEqual(get_user_max_contacts_per_agent(self.user), 17)
