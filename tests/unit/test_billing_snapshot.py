from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError
from django.test import SimpleTestCase, tag

from api.services.billing_snapshot import get_billing_snapshot_for_owner


@tag("batch_billing")
class BillingSnapshotTests(SimpleTestCase):
    def test_operational_error_does_not_block_snapshot(self):
        owner = SimpleNamespace(id=123)

        with patch(
            "api.services.billing_snapshot.get_owner_plan_context",
            side_effect=OperationalError("db unavailable"),
        ), patch(
            "api.services.billing_snapshot.is_owner_currently_in_trial",
            return_value=True,
        ):
            snapshot = get_billing_snapshot_for_owner(owner)

        self.assertEqual(
            snapshot,
            {
                "billing_plan": None,
                "billing_is_trial": True,
            },
        )

    def test_value_error_from_plan_context_propagates(self):
        owner = SimpleNamespace(id=123)

        with patch(
            "api.services.billing_snapshot.get_owner_plan_context",
            side_effect=ValueError("bad plan context"),
        ):
            with self.assertRaises(ValueError):
                get_billing_snapshot_for_owner(owner)

    def test_missing_owner_id_returns_empty_snapshot_without_lookups(self):
        owner = SimpleNamespace()

        with patch("api.services.billing_snapshot.get_owner_plan_context") as mock_plan_context, patch(
            "api.services.billing_snapshot.is_owner_currently_in_trial"
        ) as mock_trial_state:
            snapshot = get_billing_snapshot_for_owner(owner)

        self.assertEqual(
            snapshot,
            {
                "billing_plan": None,
                "billing_is_trial": None,
            },
        )
        mock_plan_context.assert_not_called()
        mock_trial_state.assert_not_called()
