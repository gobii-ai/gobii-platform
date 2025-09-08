from decimal import Decimal
from django.test import TestCase, override_settings, tag

from util.tool_costs import get_tool_credit_cost


@tag("batch_tool_costs")
class ToolCostTests(TestCase):
    @override_settings(CREDITS_PER_TASK=Decimal("0.50"), TOOL_CREDIT_COSTS={"search_web": Decimal("0.10")})
    def test_exact_match_uses_override(self):
        self.assertEqual(get_tool_credit_cost("search_web"), Decimal("0.10"))

    @override_settings(CREDITS_PER_TASK=Decimal("0.50"), TOOL_CREDIT_COSTS={"search_web": Decimal("0.10")})
    def test_missing_tool_uses_default(self):
        self.assertEqual(get_tool_credit_cost("unknown_tool"), Decimal("0.50"))

    @override_settings(CREDITS_PER_TASK=Decimal("1.00"), TOOL_CREDIT_COSTS={"HTTP_REQUEST": "0.2"})
    def test_case_insensitive_and_coerce(self):
        self.assertEqual(get_tool_credit_cost("http_request"), Decimal("0.2"))

