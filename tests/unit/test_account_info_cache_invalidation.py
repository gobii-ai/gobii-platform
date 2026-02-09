from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, RequestFactory, tag
from django.utils import timezone

from api.models import TaskCredit
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNamesChoices
from pages.account_info_cache import account_info_cache_key
from pages.context_processors import account_info


@tag("batch_billing")
class AccountInfoCacheInvalidationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="cache-user",
            email="cache-user@example.com",
            password="pw12345",
        )

    def test_task_credit_save_invalidates_account_info_cache(self):
        now = timezone.now()

        # Some environments auto-grant a free plan block on signup; void it so the test
        # has a deterministic baseline without fighting uniqueness constraints.
        TaskCredit.objects.filter(user=self.user).update(voided=True)

        TaskCredit.objects.create(
            user=self.user,
            credits=Decimal("100"),
            credits_used=Decimal("0"),
            granted_date=now - timedelta(minutes=1),
            expiration_date=now + timedelta(days=30),
            plan=PlanNamesChoices.STARTUP.value,
            additional_task=False,
            grant_type=GrantTypeChoices.TASK_PACK,
            voided=False,
        )

        request = self.factory.get("/")
        request.user = self.user

        payload = account_info(request)
        self.assertEqual(payload["account"]["usage"]["tasks_available"], 100)

        key = account_info_cache_key(self.user.id)
        self.assertIsNotNone(cache.get(key))

        TaskCredit.objects.create(
            user=self.user,
            credits=Decimal("500"),
            credits_used=Decimal("0"),
            granted_date=now - timedelta(seconds=30),
            expiration_date=now + timedelta(days=30),
            plan=PlanNamesChoices.STARTUP.value,
            additional_task=False,
            grant_type=GrantTypeChoices.TASK_PACK,
            voided=False,
        )

        # Cache should be invalidated immediately via signal.
        self.assertIsNone(cache.get(key))

        payload2 = account_info(request)
        self.assertEqual(payload2["account"]["usage"]["tasks_available"], 600)
