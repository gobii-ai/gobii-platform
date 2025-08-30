# tests/test_billing_service.py
from datetime import date

from django.test import TestCase, tag
from dateutil.relativedelta import relativedelta

from billing.services import BillingService


@tag("batch_billing")
class BillingServiceComputeNextBillingDateTests(TestCase):
    """Unit tests for BillingService.compute_next_billing_date."""

    # ────────────────────────────────────────────────
    # happy-path and edge cases
    # ────────────────────────────────────────────────
    def test_general_cases(self):
        cases = [
            # anchor, reference, expected
            (15, date(2024, 1, 10), date(2024, 1, 15)),   # later in same month
            (15, date(2024, 1, 15), date(2024, 2, 15)),   # exactly on anchor → next month
            (10, date(2024, 1, 15), date(2024, 2, 10)),   # anchor already passed
            (31, date(2024, 1, 15), date(2024, 1, 31)),   # EOM in long month
            (31, date(2023, 2, 15), date(2023, 2, 28)),   # Feb (non-leap) clamp
            (31, date(2024, 2, 15), date(2024, 2, 29)),   # Feb (leap) clamp
            (31, date(2024, 4, 15), date(2024, 4, 30)),   # April (30 days) clamp
            (31, date(2025, 2, 28), date(2025, 3, 31)),   # Feb → Mar roll-forward
            (1,  date(2024, 6, 15), date(2024, 7, 1)),    # anchor day 1 mid-month
            (1,  date(2024, 6, 1),  date(2024, 7, 1)),    # anchor day 1 at start
            (15, date(2024, 12, 20), date(2025, 1, 15)),  # cross year boundary
        ]
        for anchor, reference, expected in cases:
            with self.subTest(anchor=anchor, reference=reference):
                self.assertEqual(
                    BillingService.compute_next_billing_date(anchor, reference),
                    expected,
                )

    def test_default_reference_today(self):
        """If reference is None, ‘today’ is used."""
        anchor = 1
        today = date.today()

        result = BillingService.compute_next_billing_date(anchor)

        expected = (
            today.replace(day=anchor)
            if today.day < anchor
            else (today + relativedelta(months=1)).replace(day=anchor)
        )
        self.assertEqual(result, expected)


@tag("batch_billing")
class BillingServiceCurrentPeriodTests(TestCase):
    """Unit tests for BillingService.get_current_billing_period_from_day."""

    def test_general_cases(self):
        cases = [
            # billing_day, today, expected_start, expected_end
            (15, date(2024, 6, 20), date(2024, 6, 15), date(2024, 7, 14)),
            (15, date(2024, 6, 10), date(2024, 5, 15), date(2024, 6, 14)),
            (15, date(2024, 6, 15), date(2024, 6, 15), date(2024, 7, 14)),
            (31, date(2024, 4, 20), date(2024, 3, 31), date(2024, 4, 29)),  # <- 30-Apr bill → 29-Apr end
            (31, date(2023, 2, 20), date(2023, 1, 31), date(2023, 2, 27)),  # <- 28-Feb bill → 27-Feb end
            (15, date(2024, 12, 20), date(2024, 12, 15), date(2025, 1, 14)),
            (15, date(2024, 1, 10),  date(2023, 12, 15), date(2024, 1, 14)),
            (1,  date(2024, 6, 15),  date(2024, 6, 1),   date(2024, 6, 30)),
            (1,  date(2024, 6, 1),   date(2024, 6, 1),   date(2024, 6, 30)),
            (29, date(2024, 2, 29),  date(2024, 2, 29),  date(2024, 3, 28)),
        ]
        for billing_day, today, exp_start, exp_end in cases:
            with self.subTest(billing_day=billing_day, today=today):
                start, end = BillingService.get_current_billing_period_from_day(
                    billing_day, today
                )
                self.assertEqual(start, exp_start)
                self.assertEqual(end, exp_end)

    def test_period_sequence_consistency(self):
        """Ensure end-of-period + 1 day equals next period start."""
        billing_day = 15
        today = date(2024, 6, 20)

        start, end = BillingService.get_current_billing_period_from_day(
            billing_day, today
        )
        next_start = end + relativedelta(days=1)

        self.assertEqual(next_start, start + relativedelta(months=1))

    def test_boundary_conditions(self):
        test_cases = [
            # billing_day, today, expected_start, expected_end
            (31, date(2024, 1, 31), date(2024, 1, 31), date(2024, 2, 28)),
            (31, date(2023, 1, 31), date(2023, 1, 31), date(2023, 2, 27)),
            (30, date(2024, 4, 30), date(2024, 4, 30), date(2024, 5, 29)),
            (28, date(2024, 2, 28), date(2024, 2, 28), date(2024, 3, 27)),
        ]
        for billing_day, today, exp_start, exp_end in test_cases:
            with self.subTest(billing_day=billing_day, today=today):
                start, end = BillingService.get_current_billing_period_from_day(
                    billing_day, today
                )
                self.assertEqual(start, exp_start)
                self.assertEqual(end, exp_end)


@tag("batch_billing")
class BillingServiceValidationTests(TestCase):
    """Invalid-input and behaviour-parity checks."""

    def test_invalid_billing_days_raise(self):
        for bad_day in (0, -1, 32):
            with self.subTest(bad_day=bad_day):
                with self.assertRaises(ValueError):
                    BillingService.compute_next_billing_date(bad_day, date(2024, 1, 15))
                with self.assertRaises(ValueError):
                    BillingService.get_current_billing_period_from_day(
                        bad_day, date(2024, 1, 15)
                    )

    def test_stripe_like_last_valid_day_sequence(self):
        """31-day anchor should follow Stripe’s clamp pattern."""
        anchor = 31

        jan_31 = BillingService.compute_next_billing_date(anchor, date(2024, 1, 15))
        self.assertEqual(jan_31, date(2024, 1, 31))

        feb_29 = BillingService.compute_next_billing_date(anchor, jan_31)
        self.assertEqual(feb_29, date(2024, 2, 29))  # leap year

        mar_31 = BillingService.compute_next_billing_date(anchor, feb_29)
        self.assertEqual(mar_31, date(2024, 3, 31))
