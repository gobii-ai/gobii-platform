from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

import api.evals.loader  # noqa: F401 - registers scenarios and suites
from api.evals.registry import ScenarioRegistry
from api.evals.scenarios.bitcoin_price_multiturn import bitcoin_tool_calls_include_supported_price_api
from api.evals.scenarios.native_http import decoded_url, query_value
from api.evals.scenarios.sqlite_tool_results import _source_fetch_counts
from api.evals.tool_params import resolved_tool_param
from api.models import BrowserUseAgent, PersistentAgent, PersistentAgentLinkReference


@tag("eval_sim")
class ResolvedToolParamTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="eval-link-params@example.com")
        browser_agent = BrowserUseAgent.objects.create(user=user, name="Eval Link Params BA")
        self.agent = PersistentAgent.objects.create(
            user=user,
            name="Eval Link Params Agent",
            charter="Test eval URL comparisons.",
            browser_use_agent=browser_agent,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        other_browser_agent = BrowserUseAgent.objects.create(user=user, name="Other Eval Link Params BA")
        self.other_agent = PersistentAgent.objects.create(
            user=user,
            name="Other Eval Link Params Agent",
            charter="Test agent isolation.",
            browser_use_agent=other_browser_agent,
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )

    def _reference_call(self, url, *, agent=None, tool_name="http_request"):
        owner = agent or self.agent
        reference = PersistentAgentLinkReference.objects.create(
            agent=owner,
            url=url,
            source_kind=PersistentAgentLinkReference.SourceKind.INBOUND_MESSAGE,
        )
        call = SimpleNamespace(
            tool_name=tool_name,
            tool_params={"url": f"$[link:{reference.public_id}]"},
            step=SimpleNamespace(agent=self.agent),
            status="complete",
        )
        return call

    def test_resolves_reference_for_exact_and_query_aware_eval_assertions(self):
        url = "https://api.example.test/items?region=west&limit=2#results"
        call = self._reference_call(url)

        self.assertEqual(resolved_tool_param(call, "url"), url)
        self.assertEqual(decoded_url(call), url.lower())
        self.assertEqual(query_value(call, "region"), "west")

        scenario = ScenarioRegistry.get("common_use_case_002_fetch_status_json")
        self.assertTrue(scenario._calls_match_expected_params([call], {"url": url}))

    def test_source_fetch_counts_accept_reference_but_not_foreign_reference(self):
        url = "https://sources.example.test/items/1?view=full#details"
        call = self._reference_call(url, tool_name="mcp_brightdata_scrape_as_markdown")
        foreign_call = self._reference_call(url, agent=self.other_agent)

        self.assertEqual(
            _source_fetch_counts([call], tool_names={call.tool_name}, source_urls=[url]),
            {url: 1},
        )
        self.assertEqual(resolved_tool_param(foreign_call, "url"), foreign_call.tool_params["url"])

    def test_bitcoin_url_assertion_accepts_reference(self):
        call = self._reference_call(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
        )

        self.assertTrue(bitcoin_tool_calls_include_supported_price_api([call]))

    def test_does_not_resolve_reference_id_embedded_in_raw_url(self):
        url = "https://sources.example.test/items/2"
        call = self._reference_call(url)
        public_id = call.tool_params["url"].removeprefix("$[link:").removesuffix("]")
        call.tool_params["url"] = f"https://app.example.test/{public_id}"

        self.assertEqual(resolved_tool_param(call, "url"), call.tool_params["url"])

    def test_raw_and_malformed_values_remain_strict(self):
        raw_url = "https://api.example.test/raw.json"
        raw_call = SimpleNamespace(tool_params={"url": raw_url}, step=SimpleNamespace(agent=self.agent))
        malformed_call = SimpleNamespace(
            tool_params={"url": "$[link:not-valid]"},
            step=SimpleNamespace(agent=self.agent),
        )

        self.assertEqual(resolved_tool_param(raw_call, "url"), raw_url)
        self.assertEqual(resolved_tool_param(malformed_call, "url"), "$[link:not-valid]")
