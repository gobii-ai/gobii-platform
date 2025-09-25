from decimal import Decimal

from django.test import TestCase, tag
from django.utils import timezone

from api.models import Organization, TaskCredit
from console.org_billing_helpers import build_org_billing_overview
from django.contrib.auth import get_user_model


User = get_user_model()


@tag('batch_org_billing')
class OrganizationBillingHelperTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='pw12345',
        )
        self.organization = Organization.objects.create(
            name='Helpers Inc',
            slug='helpers-inc',
            created_by=self.owner,
        )

    def test_build_org_billing_overview_returns_credit_summary(self):
        TaskCredit.objects.create(
            organization=self.organization,
            credits=Decimal('100'),
            credits_used=Decimal('25'),
            granted_date=timezone.now() - timezone.timedelta(days=1),
            expiration_date=timezone.now() + timezone.timedelta(days=5),
        )

        data = build_org_billing_overview(self.organization)

        self.assertEqual(data['credits']['granted'], 100.0)
        self.assertEqual(data['credits']['used'], 25.0)
        self.assertGreaterEqual(data['credits']['available'], 0.0)
        self.assertIn('period', data)
        self.assertEqual(data['seats']['purchased'], self.organization.billing.purchased_seats)
