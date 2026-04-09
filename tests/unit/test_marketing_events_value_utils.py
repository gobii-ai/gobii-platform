from django.test import SimpleTestCase, tag

from constants.plans import PlanNames
from marketing_events.value_utils import (
    calculate_start_trial_values,
    resolve_start_trial_conversion_rate,
)


@tag("batch_marketing_events")
class StartTrialValueUtilsTests(SimpleTestCase):
    def test_calculates_values_from_numeric_inputs(self):
        predicted_ltv, conversion_value = calculate_start_trial_values(
            30,
            ltv_multiple=2,
            conversion_rate=0.3,
        )

        self.assertEqual(predicted_ltv, 60.0)
        self.assertEqual(conversion_value, 18.0)

    def test_accepts_numeric_strings(self):
        predicted_ltv, conversion_value = calculate_start_trial_values(
            "19.99",
            ltv_multiple="2.5",
            conversion_rate="0.3",
        )

        self.assertAlmostEqual(predicted_ltv, 49.975)
        self.assertAlmostEqual(conversion_value, 14.9925)

    def test_returns_none_tuple_for_invalid_input(self):
        predicted_ltv, conversion_value = calculate_start_trial_values(
            "not_a_number",
            ltv_multiple=2,
            conversion_rate=0.3,
        )

        self.assertIsNone(predicted_ltv)
        self.assertIsNone(conversion_value)

    def test_returns_none_tuple_for_none_or_bool_inputs(self):
        for kwargs in (
            {"base_value": None, "ltv_multiple": 2, "conversion_rate": 0.3},
            {"base_value": True, "ltv_multiple": 2, "conversion_rate": 0.3},
            {"base_value": 30, "ltv_multiple": False, "conversion_rate": 0.3},
            {"base_value": 30, "ltv_multiple": 2, "conversion_rate": True},
        ):
            with self.subTest(kwargs=kwargs):
                predicted_ltv, conversion_value = calculate_start_trial_values(**kwargs)

                self.assertIsNone(predicted_ltv)
                self.assertIsNone(conversion_value)

    def test_allows_zero_conversion_rate(self):
        predicted_ltv, conversion_value = calculate_start_trial_values(
            30,
            ltv_multiple=2,
            conversion_rate=0,
        )

        self.assertEqual(predicted_ltv, 60.0)
        self.assertEqual(conversion_value, 0.0)

    def test_preserves_negative_values(self):
        predicted_ltv, conversion_value = calculate_start_trial_values(
            -30,
            ltv_multiple=2,
            conversion_rate=0.3,
        )

        self.assertEqual(predicted_ltv, -60.0)
        self.assertEqual(conversion_value, -18.0)

    def test_resolve_start_trial_conversion_rate_uses_scale_override(self):
        self.assertEqual(
            resolve_start_trial_conversion_rate(
                PlanNames.SCALE,
                default_rate=0.322,
                scale_rate=0.22,
            ),
            0.22,
        )

    def test_resolve_start_trial_conversion_rate_uses_default_for_non_scale(self):
        self.assertEqual(
            resolve_start_trial_conversion_rate(
                PlanNames.STARTUP,
                default_rate=0.322,
                scale_rate=0.22,
            ),
            0.322,
        )
