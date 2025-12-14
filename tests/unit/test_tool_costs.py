from decimal import Decimal
from django.test import TestCase, tag

from api.models import CommsChannel, TaskCreditConfig, ToolCreditCost
from util.tool_costs import (
    clear_tool_credit_cost_cache,
    get_default_task_credit_cost,
    get_tool_credit_cost,
    get_most_expensive_tool_cost,
    get_tool_credit_cost_for_channel,
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
        ToolCreditCost.objects.create(tool_name="mcp_brightdata_search_engine", credit_cost=Decimal("0.10"))
        clear_tool_credit_cost_cache()

        self.assertEqual(get_tool_credit_cost("mcp_brightdata_search_engine"), Decimal("0.10"))

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
        override = ToolCreditCost.objects.create(tool_name="mcp_brightdata_search_engine", credit_cost=Decimal("0.10"))
        self.assertEqual(get_tool_credit_cost("mcp_brightdata_search_engine"), Decimal("0.10"))

        override.credit_cost = Decimal("0.25")
        override.save()

        self.assertEqual(get_tool_credit_cost("mcp_brightdata_search_engine"), Decimal("0.25"))

    def test_get_tool_cost_for_email_channel(self):
        ToolCreditCost.objects.create(tool_name="send_email", credit_cost=Decimal("0.90"))

        self.assertEqual(
            get_tool_credit_cost_for_channel(CommsChannel.EMAIL),
            Decimal("0.90"),
        )

    def test_get_tool_cost_for_sms_channel_string(self):
        ToolCreditCost.objects.create(tool_name="send_sms", credit_cost=Decimal("0.35"))

        self.assertEqual(
            get_tool_credit_cost_for_channel("sms"),
            Decimal("0.35"),
        )

    def test_get_tool_cost_for_unknown_channel_defaults(self):
        self.assertEqual(
            get_tool_credit_cost_for_channel("discord"),
            Decimal("0.50"),
        )

    def test_get_most_expensive_tool_cost_uses_highest_db_value(self):
        ToolCreditCost.objects.bulk_create(
            [
                ToolCreditCost(tool_name="mcp_brightdata_search_engine", credit_cost=Decimal("0.10")),
                ToolCreditCost(tool_name="sqlite_batch", credit_cost=Decimal("0.80")),
            ]
        )
        clear_tool_credit_cost_cache()

        self.assertEqual(get_most_expensive_tool_cost(), Decimal("0.80"))

    def test_get_most_expensive_tool_cost_defaults_when_no_override_above_default(self):
        config = TaskCreditConfig.objects.get(singleton_id=1)
        config.default_task_cost = Decimal("0.75")
        config.save()

        ToolCreditCost.objects.create(tool_name="mcp_brightdata_search_engine", credit_cost=Decimal("0.10"))
        clear_tool_credit_cost_cache()

        self.assertEqual(get_most_expensive_tool_cost(), Decimal("0.75"))
