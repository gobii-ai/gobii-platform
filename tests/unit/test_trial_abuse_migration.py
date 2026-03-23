import importlib
from datetime import timedelta
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TestCase, tag
from django.utils import timezone

from api.models import UserAttribution, UserIdentitySignal, UserIdentitySignalTypeChoices


User = get_user_model()


@tag("batch_pages")
class BackfillUserIdentitySignalsMigrationTests(TestCase):
    def setUp(self):
        self.migration = importlib.import_module(
            "api.migrations.0323_backfill_user_identity_signals_from_attribution"
        )
        self.schema_editor = SimpleNamespace(connection=connections["default"])

    @tag("batch_pages")
    def test_backfill_user_identity_signals_from_attribution(self):
        user = User.objects.create_user(
            username="backfill@example.com",
            email="backfill@example.com",
            password="pw",
        )
        observed_at = timezone.now() - timedelta(days=1)
        UserAttribution.objects.create(
            user=user,
            ga_client_id="GA1.2.123.456",
            fbp="fb.1.123.abcdef",
            last_client_ip="198.51.100.24",
            last_touch_at=observed_at,
        )

        self.migration.backfill_user_identity_signals(apps, self.schema_editor)
        self.migration.backfill_user_identity_signals(apps, self.schema_editor)

        signals = {
            (signal.signal_type, signal.signal_value): signal
            for signal in UserIdentitySignal.objects.filter(user=user)
        }
        self.assertSetEqual(
            set(signals),
            {
                (UserIdentitySignalTypeChoices.GA_CLIENT_ID, "123.456"),
                (UserIdentitySignalTypeChoices.FBP, "fb.1.123.abcdef"),
                (UserIdentitySignalTypeChoices.IP_EXACT, "198.51.100.24"),
                (UserIdentitySignalTypeChoices.IP_PREFIX, "198.51.100.0/24"),
            },
        )

        for signal in signals.values():
            self.assertEqual(signal.first_seen_source, self.migration.BACKFILL_SOURCE)
            self.assertEqual(signal.last_seen_source, self.migration.BACKFILL_SOURCE)
            self.assertEqual(signal.observation_count, 1)
            self.assertEqual(signal.first_seen_at, observed_at)
            self.assertEqual(signal.last_seen_at, observed_at)
