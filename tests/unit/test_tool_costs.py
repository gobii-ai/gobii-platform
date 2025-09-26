from decimal import Decimal
from django.test import TestCase, override_settings, tag

from django.test import TestCase, tag
from util.tool_costs import get_tool_credit_cost, get_most_expensive_tool_cost

from api.models import TaskCreditConfig, ToolCreditCost
from util.tool_costs import (
    clear_tool_credit_cost_cache,
    get_default_task_credit_cost,
    get_tool_credit_cost,
)


@tag('batch_tool_costs')
class ToolCostTests(TestCase):
    def setUp(self):
        clear_tool_credit_cost_cache()
        TaskCreditConfig.objects.update_or_create(
            singleton_id=1,
            defaults={"default_task_cost": Decimal("0.50")},
        )
        ToolCreditCost.objects.all().delete()

    def test_exact_match_uses_override(self):
        ToolCreditCost.objects.create(tool_name="search_web", credit_cost=Decimal("0.10"))
        clear_tool_credit_cost_cache()

        self.assertEqual(get_tool_credit_cost("search_web"), Decimal("0.10"))

    def test_missing_tool_uses_default(self):
        self.assertEqual(get_tool_credit_cost("unknown_tool"), Decimal("0.50"))

    def test_case_insensitive_lookup(self):
        ToolCreditCost.objects.create(tool_name="http_request", credit_cost=Decimal("0.20"))
        clear_tool_credit_cost_cache()

        self.assertEqual(get_tool_credit_cost("HTTP_REQUEST"), Decimal("0.20"))

    def test_default_cost_updates_after_config_change(self):
        # Warm cache with original value
        self.assertEqual(get_default_task_credit_cost(), Decimal("0.50"))

        config = TaskCreditConfig.objects.get(singleton_id=1)
        config.default_task_cost = Decimal("0.75")
        config.save()

        self.assertEqual(get_default_task_credit_cost(), Decimal("0.75"))

    def test_cache_refreshes_after_override_change(self):
        override = ToolCreditCost.objects.create(tool_name="search_web", credit_cost=Decimal("0.10"))
        self.assertEqual(get_tool_credit_cost("search_web"), Decimal("0.10"))

        override.credit_cost = Decimal("0.25")
        override.save()

        self.assertEqual(get_tool_credit_cost("search_web"), Decimal("0.25"))
    @override_settings(
        CREDITS_PER_TASK=Decimal("0.50"),
        TOOL_CREDIT_COSTS={
            "search_web": Decimal("0.10"),
            "sqlite_batch": Decimal("0.80"),
            "bad": "not_a_number",
        },
    )
    def test_get_most_expensive_tool_cost_uses_highest_valid_value(self):
        self.assertEqual(get_most_expensive_tool_cost(), Decimal("0.80"))

    @override_settings(
        CREDITS_PER_TASK=Decimal("0.75"),
        TOOL_CREDIT_COSTS={"search_web": Decimal("0.10")},
    )
    def test_get_most_expensive_tool_cost_defaults_when_no_higher_value(self):
        self.assertEqual(get_most_expensive_tool_cost(), Decimal("0.75"))
