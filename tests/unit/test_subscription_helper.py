from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from unittest.mock import patch, MagicMock

from api.models import (
    BrowserUseAgent,
    PersistentAgent,
    UserBilling,
    UserFlags,
    Organization,
    OrganizationBilling,
    TaskCredit,
)
from constants.plans import PlanNames
from constants.grant_types import GrantTypeChoices
from util.subscription_helper import (
    mark_user_billing_with_plan,
    mark_organization_billing_with_plan,
    downgrade_organization_to_free_plan,
    get_users_due_for_monthly_grant,
    ensure_single_individual_subscription,
    get_existing_individual_subscriptions,
    get_subscription_base_price,
)
from util.trial_enforcement import PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH
from waffle.models import Switch


User = get_user_model()


@tag("batch_subscription")
class MarkUserBillingWithPlanTests(TestCase):
    """Tests for the mark_user_billing_with_plan helper."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="planuser@example.com",
            email="planuser@example.com",
            password="testpass123",
        )

    @tag("batch_subscription")
    def test_creates_billing_record_when_missing(self):
        """A billing record is created when one does not exist."""
        UserBilling.objects.filter(user=self.user).delete()

        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2024, 5, 9)
            mark_user_billing_with_plan(self.user, PlanNames.STARTUP)

        billing = UserBilling.objects.get(user=self.user)
        self.assertEqual(billing.subscription, PlanNames.STARTUP)
        self.assertEqual(billing.billing_cycle_anchor, 9)

    @tag("batch_subscription")
    def test_updates_existing_record_without_duplication(self):
        """Existing billing records are updated in place without creating duplicates."""
        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2024, 6, 2)
            mark_user_billing_with_plan(self.user, PlanNames.STARTUP)

        self.assertEqual(UserBilling.objects.filter(user=self.user).count(), 1)
        billing = UserBilling.objects.get(user=self.user)
        self.assertEqual(billing.subscription, PlanNames.STARTUP)
        self.assertEqual(billing.billing_cycle_anchor, 2)

        # Call again with a different plan; anchor should update and still only one record
        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2024, 7, 4)
            mark_user_billing_with_plan(self.user, PlanNames.FREE)

        self.assertEqual(UserBilling.objects.filter(user=self.user).count(), 1)
        billing.refresh_from_db()
        self.assertEqual(billing.subscription, PlanNames.FREE)
        self.assertEqual(billing.billing_cycle_anchor, 4)

    @tag("batch_subscription")
    def test_update_anchor_false_keeps_existing_anchor(self):
        """The billing cycle anchor remains unchanged when update_anchor is False."""
        billing = UserBilling.objects.get(user=self.user)
        billing.billing_cycle_anchor = 5
        billing.subscription = PlanNames.FREE
        billing.save()

        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2024, 8, 30)
            mark_user_billing_with_plan(self.user, PlanNames.STARTUP, update_anchor=False)

        billing.refresh_from_db()
        self.assertEqual(billing.subscription, PlanNames.STARTUP)
        self.assertEqual(billing.billing_cycle_anchor, 5)

    @tag("batch_subscription")
    def test_upgrade_clears_agent_daily_credit_limit(self):
        """Daily credit caps are removed from agents when the user upgrades."""
        billing = UserBilling.objects.get(user=self.user)
        billing.subscription = PlanNames.FREE
        billing.save(update_fields=["subscription"])

        with patch.object(BrowserUseAgent, "select_random_proxy", return_value=None):
            browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Limitless Agent")

        agent = PersistentAgent.objects.create(
            user=self.user,
            name="Capped Agent",
            charter="Upgrade charter",
            browser_use_agent=browser_agent,
            daily_credit_limit=5,
        )

        mark_user_billing_with_plan(self.user, PlanNames.STARTUP)

        agent.refresh_from_db()
        self.assertIsNone(agent.daily_credit_limit)


@tag("batch_subscription")
class MarkOrganizationBillingWithPlanTests(TestCase):
    """Ensure organization billing records are synced with plan updates."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="ownerpass123",
        )
        self.organization = Organization.objects.create(
            name="Acme Corp",
            slug="acme-corp",
            created_by=self.owner,
        )

    def test_creates_and_updates_billing_record(self):
        OrganizationBilling.objects.filter(organization=self.organization).delete()

        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2025, 3, 15, tzinfo=datetime_timezone.utc)
            mark_organization_billing_with_plan(self.organization, PlanNames.ORG_TEAM)

        billing = OrganizationBilling.objects.get(organization=self.organization)
        self.assertEqual(billing.subscription, PlanNames.ORG_TEAM)
        self.assertEqual(billing.billing_cycle_anchor, 15)

        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2025, 4, 2, tzinfo=datetime_timezone.utc)
            mark_organization_billing_with_plan(self.organization, PlanNames.ORG_TEAM, update_anchor=False)

        billing.refresh_from_db()
        self.assertEqual(billing.subscription, PlanNames.ORG_TEAM)
        self.assertEqual(billing.billing_cycle_anchor, 15)

    def test_downgrade_sets_timestamp(self):
        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2025, 5, 5, tzinfo=datetime_timezone.utc)
            mark_organization_billing_with_plan(self.organization, PlanNames.ORG_TEAM)

        with patch("util.subscription_helper.timezone.now") as mock_now:
            mock_now.return_value = datetime(2025, 6, 1, tzinfo=datetime_timezone.utc)
            downgrade_organization_to_free_plan(self.organization)

        billing = OrganizationBilling.objects.get(organization=self.organization)
        self.assertEqual(billing.subscription, PlanNames.FREE)
        self.assertEqual(billing.billing_cycle_anchor, 5)
        self.assertEqual(billing.downgraded_at, datetime(2025, 6, 1, tzinfo=datetime_timezone.utc))


@tag("batch_subscription")
class GetUsersDueForMonthlyGrantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="due-user@example.com",
            email="due-user@example.com",
            password="password123",
        )
        self.other = User.objects.create_user(
            username="current-user@example.com",
            email="current-user@example.com",
            password="password123",
        )
        self.user.task_credits.all().delete()
        self.other.task_credits.all().delete()
        UserBilling.objects.filter(user__in=[self.user, self.other]).delete()

    @tag("batch_subscription")
    def test_returns_user_when_current_period_missing_grant(self):
        with timezone.override("UTC"):
            UserBilling.objects.update_or_create(
                user=self.user,
                defaults={"billing_cycle_anchor": 6, "subscription": PlanNames.FREE},
            )
            TaskCredit.objects.create(
                user=self.user,
                credits=10,
                credits_used=0,
                granted_date=timezone.make_aware(datetime(2025, 10, 6)),
                expiration_date=timezone.make_aware(datetime(2025, 11, 6)),
                plan=PlanNames.FREE,
                grant_type=GrantTypeChoices.PLAN,
                additional_task=False,
                voided=False,
            )

            with patch("util.subscription_helper.timezone.now") as mock_now:
                mock_now.return_value = datetime(2025, 11, 6, tzinfo=datetime_timezone.utc)
                results = get_users_due_for_monthly_grant()

        self.assertIn(self.user, results)

    @tag("batch_subscription")
    def test_skips_user_with_grant_in_current_period(self):
        with timezone.override("UTC"):
            UserBilling.objects.update_or_create(
                user=self.other,
                defaults={"billing_cycle_anchor": 6, "subscription": PlanNames.FREE},
            )
            TaskCredit.objects.create(
                user=self.other,
                credits=10,
                credits_used=0,
                granted_date=timezone.make_aware(datetime(2025, 11, 6)),
                expiration_date=timezone.make_aware(datetime(2025, 12, 6)),
                plan=PlanNames.FREE,
                grant_type=GrantTypeChoices.PLAN,
                additional_task=False,
                voided=False,
            )

            with patch("util.subscription_helper.timezone.now") as mock_now:
                mock_now.return_value = datetime(2025, 11, 6, tzinfo=datetime_timezone.utc)
                results = get_users_due_for_monthly_grant()

        self.assertNotIn(self.other, results)

    @override_settings(PERSONAL_FREE_TRIAL_ENFORCEMENT_ENABLED=True)
    def test_enforcement_only_returns_grandfathered_free_users(self):
        with timezone.override("UTC"):
            UserBilling.objects.update_or_create(
                user=self.user,
                defaults={"billing_cycle_anchor": 6, "subscription": PlanNames.FREE},
            )
            UserBilling.objects.update_or_create(
                user=self.other,
                defaults={"billing_cycle_anchor": 6, "subscription": PlanNames.FREE},
            )
            UserFlags.objects.create(user=self.user, is_freemium_grandfathered=True)

            with patch("util.subscription_helper.timezone.now") as mock_now:
                mock_now.return_value = datetime(2025, 11, 6, tzinfo=datetime_timezone.utc)
                results = get_users_due_for_monthly_grant()

        self.assertIn(self.user, results)
        self.assertNotIn(self.other, results)

    def test_waffle_switch_enforcement_only_returns_grandfathered_free_users(self):
        Switch.objects.update_or_create(
            name=PERSONAL_FREE_TRIAL_ENFORCEMENT_WAFFLE_SWITCH,
            defaults={"active": True},
        )

        with timezone.override("UTC"):
            UserBilling.objects.update_or_create(
                user=self.user,
                defaults={"billing_cycle_anchor": 6, "subscription": PlanNames.FREE},
            )
            UserBilling.objects.update_or_create(
                user=self.other,
                defaults={"billing_cycle_anchor": 6, "subscription": PlanNames.FREE},
            )
            UserFlags.objects.create(user=self.user, is_freemium_grandfathered=True)

            with patch("util.subscription_helper.timezone.now") as mock_now:
                mock_now.return_value = datetime(2025, 11, 6, tzinfo=datetime_timezone.utc)
                results = get_users_due_for_monthly_grant()

        self.assertIn(self.user, results)
        self.assertNotIn(self.other, results)


@tag("batch_subscription")
class EnsureSingleIndividualSubscriptionTests(TestCase):
    @patch("util.subscription_helper._ensure_stripe_ready")
    @patch("util.subscription_helper.stripe.Subscription.create")
    @patch("util.subscription_helper.get_existing_individual_subscriptions", return_value=[])
    def test_creates_subscription_when_missing(self, mock_existing, mock_create, _mock_ready):
        mock_create.return_value = {"id": "sub_new"}

        subscription, action = ensure_single_individual_subscription(
            "cus_123",
            licensed_price_id="price_base",
            metered_price_id="price_meter",
            metadata={"foo": "bar"},
            idempotency_key="idem-123",
        )

        self.assertEqual(action, "created")
        self.assertEqual(subscription, {"id": "sub_new"})
        mock_create.assert_called_once()
        create_kwargs = mock_create.call_args.kwargs
        self.assertIn({"price": "price_base", "quantity": 1}, create_kwargs.get("items") or [])
        self.assertIn({"price": "price_meter"}, create_kwargs.get("items") or [])
        self.assertEqual(create_kwargs.get("metadata"), {"foo": "bar"})
        self.assertEqual(create_kwargs.get("idempotency_key"), "idem-123")

    @patch("util.subscription_helper._ensure_stripe_ready")
    @patch("util.subscription_helper.stripe.Subscription.modify")
    @patch("util.subscription_helper.stripe.Subscription.delete")
    @patch("util.subscription_helper._individual_plan_product_ids", return_value={"prod_plan"})
    @patch("util.subscription_helper.get_existing_individual_subscriptions")
    def test_updates_existing_subscription_and_keeps_metered_item(
        self,
        mock_existing,
        mock_plan_products,
        mock_delete,
        mock_modify,
        _mock_ready,
    ):
        mock_existing.return_value = [
            {
                "id": "sub_existing",
                "metadata": {"foo": "bar"},
                "items": {
                    "data": [
                        {
                            "id": "si_base",
                            "quantity": 1,
                            "price": {
                                "id": "price_old",
                                "product": "prod_plan",
                                "usage_type": "licensed",
                            },
                        },
                        {
                            "id": "si_meter",
                            "price": {"id": "price_meter_old", "usage_type": "metered"},
                        },
                    ]
                },
            }
        ]

        mock_modify.return_value = {"id": "sub_existing"}

        subscription, action = ensure_single_individual_subscription(
            "cus_123",
            licensed_price_id="price_new",
            metered_price_id="price_meter_new",
            metadata={"baz": "qux"},
            idempotency_key="idem-456",
        )

        self.assertEqual(action, "updated")
        self.assertEqual(subscription, {"id": "sub_existing"})
        mock_delete.assert_not_called()
        self.assertEqual(mock_modify.call_count, 2)
        first_kwargs = mock_modify.call_args_list[0].kwargs
        second_kwargs = mock_modify.call_args_list[1].kwargs
        items = first_kwargs.get("items") or []
        base_item = next((i for i in items if i.get("price") == "price_new"), None)
        meter_item = next((i for i in items if i.get("price") == "price_meter_new"), None)
        self.assertIsNotNone(base_item)
        self.assertEqual(base_item.get("quantity"), 1)
        self.assertIsNotNone(meter_item)
        self.assertNotIn("metadata", first_kwargs)
        self.assertEqual(first_kwargs.get("idempotency_key"), "idem-456")
        self.assertEqual(second_kwargs.get("metadata"), {"foo": "bar", "baz": "qux"})
        self.assertEqual(second_kwargs.get("idempotency_key"), "idem-456-meta")

    @patch("util.subscription_helper._ensure_stripe_ready")
    @patch("util.subscription_helper.stripe.Subscription.modify")
    @patch("util.subscription_helper.stripe.Subscription.delete")
    @patch("util.subscription_helper._individual_plan_product_ids", return_value={"prod_plan"})
    @patch("util.subscription_helper.get_existing_individual_subscriptions")
    def test_cancels_duplicates_and_updates_newest(
        self,
        mock_existing,
        _mock_plan_products,
        mock_delete,
        mock_modify,
        _mock_ready,
    ):
        mock_existing.return_value = [
            {"id": "sub_new", "created": 200, "items": {"data": [{"id": "si_new", "price": {"id": "price_old", "product": "prod_plan", "usage_type": "licensed"}}]}},
            {"id": "sub_old", "created": 100, "items": {"data": [{"id": "si_old", "price": {"id": "price_old", "product": "prod_plan", "usage_type": "licensed"}}]}},
        ]

        ensure_single_individual_subscription(
            "cus_123",
            licensed_price_id="price_new",
            metered_price_id=None,
            metadata=None,
            idempotency_key="idem-789",
        )

        mock_delete.assert_called_once_with("sub_old", prorate=True)
        mock_modify.assert_called_once()
        self.assertEqual(mock_modify.call_args.kwargs.get("idempotency_key"), "idem-789")
        self.assertEqual(mock_modify.call_args.kwargs.get("items"), [{"id": "si_new", "price": "price_new", "quantity": 1}])

    @patch("util.subscription_helper._ensure_stripe_ready")
    @patch("util.subscription_helper.stripe.Subscription.create")
    @patch("util.subscription_helper.get_existing_individual_subscriptions", return_value=[])
    def test_create_skipped_when_disabled(self, mock_existing, mock_create, _mock_ready):
        subscription, action = ensure_single_individual_subscription(
            "cus_none",
            licensed_price_id="price_base",
            metered_price_id=None,
            metadata=None,
            idempotency_key="idem-no-create",
            create_if_missing=False,
        )

        self.assertIsNone(subscription)
        self.assertEqual(action, "absent")
        mock_existing.assert_called_once_with("cus_none")
        mock_create.assert_not_called()

    @patch("util.subscription_helper._ensure_stripe_ready")
    @patch("util.subscription_helper.stripe.Subscription.modify")
    @patch("util.subscription_helper.stripe.Subscription.delete")
    @patch("util.subscription_helper._individual_plan_product_ids", return_value={"prod_plan"})
    @patch("util.subscription_helper.get_existing_individual_subscriptions")
    def test_preserves_other_licensed_addons(
        self,
        mock_existing,
        _mock_plan_products,
        mock_delete,
        mock_modify,
        _mock_ready,
    ):
        mock_existing.return_value = [
            {
                "id": "sub_existing",
                "items": {
                    "data": [
                        {
                            "id": "si_base",
                            "quantity": 1,
                            "price": {
                                "id": "price_old",
                                "product": "prod_plan",
                                "usage_type": "licensed",
                            },
                        },
                        {
                            "id": "si_addon",
                            "quantity": 2,
                            "price": {
                                "id": "price_addon",
                                "product": "prod_addon",
                                "usage_type": "licensed",
                            },
                        },
                    ]
                },
            }
        ]

        mock_modify.return_value = {"id": "sub_existing"}

        ensure_single_individual_subscription(
            "cus_123",
            licensed_price_id="price_new",
            metered_price_id=None,
            metadata=None,
            idempotency_key="idem-addons",
        )

        mock_delete.assert_not_called()
        mock_modify.assert_called_once()
        items = mock_modify.call_args.kwargs.get("items") or []
        base_item = next((i for i in items if i.get("id") == "si_base"), None)
        addon_item = next((i for i in items if i.get("id") == "si_addon"), None)
        self.assertEqual(base_item.get("price"), "price_new")
        self.assertEqual(base_item.get("quantity"), 1)
        self.assertEqual(addon_item.get("price"), "price_addon")
        self.assertEqual(addon_item.get("quantity"), 2)

    @patch("util.subscription_helper._ensure_stripe_ready")
    @patch("util.subscription_helper._individual_plan_product_ids", return_value=set())
    @patch("util.subscription_helper._individual_plan_price_ids", return_value={"price_base"})
    @patch("util.subscription_helper.stripe.Subscription.list")
    def test_get_existing_uses_price_match_when_product_missing(self, mock_list, _mock_price_ids, _mock_product_ids, _mock_ready):
        mock_page = MagicMock()
        mock_page.auto_paging_iter.return_value = [
            {
                "id": "sub_match_price",
                "status": "active",
                "created": 5,
                "items": {"data": [{"price": {"product": "unknown", "id": "price_base"}}]},
            },
        ]
        mock_list.return_value = mock_page

        results = get_existing_individual_subscriptions("cus_price")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("id"), "sub_match_price")

    @patch("util.subscription_helper._ensure_stripe_ready")
    @patch("util.subscription_helper._individual_plan_product_ids", return_value={"prod_plan"})
    @patch("util.subscription_helper._individual_plan_price_ids", return_value={"price_base"})
    @patch("util.subscription_helper.stripe.Subscription.list")
    def test_get_existing_filters_and_sorts(self, mock_list, _mock_plan_price_ids, _mock_plan_products, _mock_ready):
        mock_page = MagicMock()
        mock_page.auto_paging_iter.return_value = [
            {"id": "sub_cancelled", "status": "canceled"},
            {
                "id": "sub_match",
                "status": "active",
                "created": 10,
                "items": {"data": [{"price": {"product": "prod_plan", "id": "price_base"}}]},
            },
            {
                "id": "sub_other",
                "status": "active",
                "created": 5,
                "items": {"data": [{"price": {"product": "prod_other"}}]},
            },
        ]
        mock_list.return_value = mock_page

        results = get_existing_individual_subscriptions("cus_321")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("id"), "sub_match")


@tag("batch_subscription")
class SubscriptionPriceExtractionTests(TestCase):
    def _mock_subscription(self, items):
        subscription = MagicMock()
        items_qs = MagicMock()
        items_qs.all.return_value = items
        subscription.items = items_qs
        subscription.id = "sub_test"
        return subscription

    def test_uses_first_non_metered_item(self):
        metered_price = MagicMock()
        metered_price.unit_amount = 150
        metered_price.currency = "usd"
        metered_item = MagicMock(price=metered_price, stripe_data={"price": {"recurring": {"usage_type": "metered"}}})

        licensed_price = MagicMock()
        licensed_price.unit_amount = 2999
        licensed_price.currency = "usd"
        licensed_item = MagicMock(price=licensed_price, stripe_data={"price": {"recurring": {"usage_type": "licensed"}}})

        subscription = self._mock_subscription([metered_item, licensed_item])

        amount, currency = get_subscription_base_price(subscription)
        self.assertEqual(amount, Decimal("29.99"))
        self.assertEqual(currency, "usd")

    def test_handles_decimal_string_amount(self):
        item = MagicMock()
        item.price = None
        item.stripe_data = {
            "price": {
                "unit_amount_decimal": "1234.5",
                "currency": "eur",
                "recurring": {"usage_type": "licensed"},
            }
        }
        subscription = self._mock_subscription([item])

        amount, currency = get_subscription_base_price(subscription)
        self.assertEqual(amount, Decimal("12.345"))
        self.assertEqual(currency, "eur")
