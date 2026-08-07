from unittest.mock import ANY, MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings, tag
from requests.exceptions import ConnectionError, Timeout
from waffle import get_waffle_flag_model

from api.agent.system_skills.defaults import CONTACTOUT_SYSTEM_SKILL
from api.agent.tools.contactout import (
    CONTACTOUT_API_URL,
    CONTACTOUT_SYSTEM_SKILL_KEY,
    CONTACTOUT_TOOL_NAME,
    COUNT_PEOPLE,
    ENRICH_COMPANY_DOMAINS,
    ENRICH_LINKEDIN_PROFILE,
    SEARCH_COMPANIES,
    SEARCH_PEOPLE,
    execute_contactout,
    get_contactout_tool,
)
from api.agent.tools.mcp_manager import MCPToolInfo, MCPToolManager
from api.agent.tools.tool_manager import (
    ToolCatalogEntry,
    ensure_default_tools_enabled,
    execute_enabled_tool,
    get_available_builtin_tool_entries,
    get_enabled_tool_definitions,
    is_parallel_safe_tool_name,
    mark_tool_enabled_without_discovery,
)
from api.models import (
    BrowserUseAgent,
    MCPServerConfig,
    PersistentAgent,
    PersistentAgentEnabledTool,
    PersistentAgentSystemSkillState,
)
from api.services.contactout_feature_flags import (
    CONTACTOUT_MCP_BLOCKED,
    filter_contactout_mcp_tools_for_agent,
)
from constants.feature_flags import CONTACTOUT_PILOT


def _response(payload=None, status_code=200, *, text="", headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.headers = headers or {}
    response.json.return_value = payload
    return response


@tag("batch_contactout")
@override_settings(CONTACTOUT_API_TOKEN="contactout-token", CONTACTOUT_REQUEST_TIMEOUT_SECONDS=17.0)
@patch("api.agent.tools.contactout.contactout_enabled_for_agent", return_value=True)
class ContactOutNativeToolTests(SimpleTestCase):
    def test_tool_definition_exposes_pilot_operations_and_safe_contact_default(self, _mock_enabled):
        function = get_contactout_tool()["function"]
        properties = function["parameters"]["properties"]

        self.assertEqual(function["name"], CONTACTOUT_TOOL_NAME)
        self.assertEqual(
            properties["operation"]["enum"],
            [
                SEARCH_PEOPLE,
                COUNT_PEOPLE,
                ENRICH_LINKEDIN_PROFILE,
                SEARCH_COMPANIES,
                ENRICH_COMPANY_DOMAINS,
            ],
        )
        self.assertFalse(properties["reveal_all_contact_info"]["default"])
        self.assertIn("cannot reveal only a subset", properties["reveal_all_contact_info"]["description"])
        self.assertIn("Availability filter only", properties["required_contact_data_types"]["description"])
        self.assertEqual(properties["people_filters"]["properties"]["page_size"]["maximum"], 25)
        self.assertEqual(properties["domains"]["maxItems"], 30)

    @patch("api.agent.tools.contactout.requests.post")
    def test_people_search_defaults_to_profile_only_data(self, mock_post, _mock_enabled):
        provider_payload = {
            "status_code": 200,
            "profiles": {"https://linkedin.com/in/ada": {"full_name": "Ada Lovelace"}},
        }
        mock_post.return_value = _response(provider_payload)

        result = execute_contactout(
            MagicMock(),
            {
                "operation": SEARCH_PEOPLE,
                "people_filters": {"job_title": ["CTO OR VP Engineering"], "page_size": 12},
            },
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["provider"], "contactout")
        self.assertEqual(result["content"], provider_payload)
        mock_post.assert_called_once_with(
            f"{CONTACTOUT_API_URL}/v1/people/search",
            headers={
                "token": "contactout-token",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "gobii-platform/contactout-native",
            },
            timeout=17.0,
            json={
                "job_title": ["CTO OR VP Engineering"],
                "page_size": 12,
                "reveal_info": False,
            },
        )

    @patch("api.agent.tools.contactout.requests.post")
    def test_people_search_reveals_all_contacts_only_with_explicit_authorization(
        self,
        mock_post,
        _mock_enabled,
    ):
        mock_post.return_value = _response({"status_code": 200, "profiles": {}})

        result = execute_contactout(
            MagicMock(),
            {
                "operation": SEARCH_PEOPLE,
                "people_filters": {"skills": ["Python AND Django"]},
                "reveal_all_contact_info": True,
                "required_contact_data_types": [
                    "personal_email",
                    "work_email",
                    "phone",
                    "work_email",
                ],
            },
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            mock_post.call_args.kwargs["json"],
            {
                "skills": ["Python AND Django"],
                "reveal_info": True,
                "data_types": ["personal_email", "work_email", "phone"],
            },
        )

    @patch("api.agent.tools.contactout.requests.post")
    def test_contact_type_filter_does_not_reveal_contact_data(self, mock_post, _mock_enabled):
        mock_post.return_value = _response({"status_code": 200, "profiles": {}})

        result = execute_contactout(
            MagicMock(),
            {
                "operation": SEARCH_PEOPLE,
                "people_filters": {"company": ["Gobii"]},
                "required_contact_data_types": ["work_email"],
            },
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            mock_post.call_args.kwargs["json"],
            {
                "company": ["Gobii"],
                "reveal_info": False,
                "data_types": ["work_email"],
            },
        )

    @patch("api.agent.tools.contactout.requests.post")
    def test_people_count_strips_search_only_controls(self, mock_post, _mock_enabled):
        mock_post.return_value = _response({"status_code": 200, "total_results": 411})

        result = execute_contactout(
            MagicMock(),
            {
                "operation": COUNT_PEOPLE,
                "people_filters": {
                    "company": ["Gobii"],
                    "page": 3,
                    "page_size": 10,
                    "detailed_experience": True,
                    "output_fields": ["full_name"],
                },
            },
        )

        self.assertEqual(result["content"]["total_results"], 411)
        mock_post.assert_called_once_with(
            f"{CONTACTOUT_API_URL}/v1/people/count",
            headers=ANY,
            timeout=17.0,
            json={"company": ["Gobii"]},
        )

    @patch("api.agent.tools.contactout.requests.get")
    def test_linkedin_enrichment_defaults_to_profile_only(self, mock_get, _mock_enabled):
        mock_get.return_value = _response({"status_code": 200, "profile": {"full_name": "Ada Lovelace"}})
        url = "https://www.linkedin.com/in/ada-lovelace"

        result = execute_contactout(
            MagicMock(),
            {"operation": ENRICH_LINKEDIN_PROFILE, "linkedin_url": url},
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            mock_get.call_args.kwargs["params"],
            {"profile": url, "profile_only": True},
        )

        execute_contactout(
            MagicMock(),
            {
                "operation": ENRICH_LINKEDIN_PROFILE,
                "linkedin_url": url,
                "reveal_all_contact_info": True,
            },
        )
        self.assertFalse(mock_get.call_args.kwargs["params"]["profile_only"])

    @patch("api.agent.tools.contactout.requests.post")
    def test_company_search_and_domain_enrichment_use_provider_contract(self, mock_post, _mock_enabled):
        mock_post.side_effect = [
            _response({"status_code": 200, "companies": [{"name": "Gobii"}]}),
            _response({"status_code": 200, "companies": {"gobii.ai": {"name": "Gobii"}}}),
        ]

        search_result = execute_contactout(
            MagicMock(),
            {
                "operation": SEARCH_COMPANIES,
                "company_filters": {"industry": ["Software"], "min_revenue": 10, "max_revenue": 100},
            },
        )
        domain_result = execute_contactout(
            MagicMock(),
            {
                "operation": ENRICH_COMPANY_DOMAINS,
                "domains": ["https://www.Gobii.ai/about", "gobii.ai", "contactout.com"],
            },
        )

        self.assertEqual(search_result["status"], "success")
        self.assertEqual(domain_result["status"], "success")
        self.assertEqual(mock_post.call_args_list[0].args[0], f"{CONTACTOUT_API_URL}/v1/company/search")
        self.assertEqual(
            mock_post.call_args_list[1].kwargs["json"],
            {"domains": ["gobii.ai", "contactout.com"]},
        )

    @patch("api.agent.tools.contactout.requests.post")
    def test_rejects_unsafe_or_incompatible_inputs_before_request(self, mock_post, _mock_enabled):
        cases = [
            {
                "operation": SEARCH_PEOPLE,
                "people_filters": {"years_in_current_role": ["1_2"], "recently_changed_jobs": True},
            },
            {
                "operation": SEARCH_PEOPLE,
                "people_filters": {"page_size": 26},
            },
            {
                "operation": SEARCH_PEOPLE,
                "people_filters": {},
                "required_contact_data_types": ["postal_address"],
            },
            {
                "operation": SEARCH_COMPANIES,
                "company_filters": {
                    "linkedin_url": ["https://linkedin.com/company/gobii"],
                    "industry": ["Software"],
                },
            },
            {
                "operation": SEARCH_COMPANIES,
                "company_filters": {"unknown": ["value"]},
            },
            {
                "operation": ENRICH_COMPANY_DOMAINS,
                "domains": ["not-a-domain"],
            },
        ]

        for params in cases:
            with self.subTest(params=params):
                result = execute_contactout(MagicMock(), params)
                self.assertEqual(result["status"], "error")
                self.assertFalse(result["retryable"])
        mock_post.assert_not_called()

    @patch("api.agent.tools.contactout.requests.get")
    def test_rejects_sales_navigator_and_recruiter_urls(self, mock_get, _mock_enabled):
        for url in (
            "https://www.linkedin.com/sales/lead/abc",
            "https://www.linkedin.com/recruiter/profile/abc",
        ):
            result = execute_contactout(
                MagicMock(),
                {"operation": ENRICH_LINKEDIN_PROFILE, "linkedin_url": url},
            )
            self.assertEqual(result["status"], "error")
            self.assertIn("not supported", result["message"])
        mock_get.assert_not_called()

    @patch("api.agent.tools.contactout.requests.post", side_effect=Timeout())
    def test_timeout_is_retryable(self, _mock_post, _mock_enabled):
        result = execute_contactout(MagicMock(), {"operation": SEARCH_PEOPLE, "people_filters": {}})

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["retryable"])
        self.assertIn("timed out", result["message"])

    @patch("api.agent.tools.contactout.requests.post", side_effect=ConnectionError("offline"))
    def test_network_failure_is_retryable_without_exposing_exception(self, _mock_post, _mock_enabled):
        result = execute_contactout(MagicMock(), {"operation": SEARCH_COMPANIES, "company_filters": {}})

        self.assertTrue(result["retryable"])
        self.assertNotIn("offline", result["message"])

    @patch("api.agent.tools.contactout.requests.post")
    def test_rate_limit_preserves_retry_after_and_is_retryable(self, mock_post, _mock_enabled):
        mock_post.return_value = _response(
            {"status_code": 429, "message": "Too Many Attempts."},
            429,
            headers={"Retry-After": "20"},
        )

        result = execute_contactout(MagicMock(), {"operation": SEARCH_PEOPLE, "people_filters": {}})

        self.assertEqual(result["status_code"], 429)
        self.assertEqual(result["retry_after"], "20")
        self.assertTrue(result["retryable"])

    @patch("api.agent.tools.contactout.requests.post")
    def test_http_error_retry_classification(self, mock_post, _mock_enabled):
        for status_code, retryable in ((400, False), (401, False), (403, False), (422, False), (500, True)):
            with self.subTest(status_code=status_code):
                mock_post.return_value = _response(
                    {"status_code": status_code, "message": "provider error"},
                    status_code,
                )
                result = execute_contactout(
                    MagicMock(),
                    {"operation": SEARCH_COMPANIES, "company_filters": {}},
                )
                self.assertEqual(result["status_code"], status_code)
                self.assertEqual(result["retryable"], retryable)

    @patch("api.agent.tools.contactout.requests.post")
    def test_body_level_provider_error_and_invalid_json_are_errors(self, mock_post, _mock_enabled):
        body_error = _response({"status_code": 403, "message": "No credits"})
        invalid_json = _response(None, text="not-json")
        invalid_json.json.side_effect = ValueError("bad json")
        mock_post.side_effect = [body_error, invalid_json]

        credit_result = execute_contactout(MagicMock(), {"operation": SEARCH_PEOPLE, "people_filters": {}})
        json_result = execute_contactout(MagicMock(), {"operation": SEARCH_COMPANIES, "company_filters": {}})

        self.assertEqual(credit_result["status_code"], 403)
        self.assertFalse(credit_result["retryable"])
        self.assertIn("No credits", credit_result["message"])
        self.assertEqual(json_result["status"], "error")
        self.assertIn("invalid JSON", json_result["message"])

    @override_settings(CONTACTOUT_API_TOKEN="")
    @patch("api.agent.tools.contactout.requests.post")
    def test_missing_platform_token_fails_before_request(self, mock_post, _mock_enabled):
        result = execute_contactout(MagicMock(), {"operation": SEARCH_PEOPLE, "people_filters": {}})

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["retryable"])
        self.assertIn("CONTACTOUT_API_TOKEN", result["message"])
        mock_post.assert_not_called()


@tag("batch_contactout")
@override_settings(CONTACTOUT_API_TOKEN="contactout-token")
class ContactOutPilotToolManagerTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="contactout-pilot@example.com")
        browser_agent = BrowserUseAgent.objects.create(user=self.user, name="ContactOut Browser")
        self.agent = PersistentAgent.objects.create(
            user=self.user,
            name="ContactOut Agent",
            charter="Test native ContactOut sourcing.",
            browser_use_agent=browser_agent,
        )
        self.flag, _ = get_waffle_flag_model().objects.get_or_create(
            name=CONTACTOUT_PILOT,
            defaults={"everyone": None},
        )

    def test_unflagged_user_cannot_discover_tool_or_render_skill(self):
        entries = get_available_builtin_tool_entries(self.agent)

        self.assertNotIn(CONTACTOUT_TOOL_NAME, entries)
        self.assertFalse(CONTACTOUT_SYSTEM_SKILL.should_render_prompt(self.agent))

    def test_flagged_user_gets_native_tool_and_skill_as_conditional_default(self):
        self.flag.users.add(self.user)

        entries = get_available_builtin_tool_entries(self.agent)
        ensure_default_tools_enabled(self.agent)

        self.assertEqual(entries[CONTACTOUT_TOOL_NAME].provider, "builtin")
        row = PersistentAgentEnabledTool.objects.get(
            agent=self.agent,
            tool_full_name=CONTACTOUT_TOOL_NAME,
        )
        self.assertEqual(row.tool_server, "builtin")
        self.assertEqual(row.tool_name, CONTACTOUT_TOOL_NAME)
        self.assertTrue(
            PersistentAgentSystemSkillState.objects.filter(
                agent=self.agent,
                skill_key=CONTACTOUT_SYSTEM_SKILL_KEY,
                is_enabled=True,
            ).exists()
        )
        definitions = get_enabled_tool_definitions(self.agent)
        self.assertIn(CONTACTOUT_TOOL_NAME, [item["function"]["name"] for item in definitions])
        self.assertTrue(CONTACTOUT_SYSTEM_SKILL.should_render_prompt(self.agent))
        self.assertTrue(is_parallel_safe_tool_name(CONTACTOUT_TOOL_NAME))

    @patch("api.agent.tools.tool_manager._get_manager")
    def test_conditional_default_does_not_use_mcp_discovery(self, mock_get_manager):
        self.flag.users.add(self.user)

        ensure_default_tools_enabled(self.agent)

        mock_get_manager.assert_not_called()

    def test_eval_override_is_scoped_to_one_agent_not_the_shared_user(self):
        self.agent.execution_environment = "eval"
        self.agent.save(update_fields=["execution_environment"])
        other_browser_agent = BrowserUseAgent.objects.create(user=self.user, name="Other Eval Browser")
        other_agent = PersistentAgent.objects.create(
            user=self.user,
            name="Other Eval Agent",
            charter="Test eval isolation.",
            browser_use_agent=other_browser_agent,
            execution_environment="eval",
        )

        result = mark_tool_enabled_without_discovery(self.agent, CONTACTOUT_TOOL_NAME)

        self.assertEqual(result["status"], "success")
        self.assertIn(CONTACTOUT_TOOL_NAME, get_available_builtin_tool_entries(self.agent))
        self.assertNotIn(CONTACTOUT_TOOL_NAME, get_available_builtin_tool_entries(other_agent))
        self.assertFalse(self.flag.users.filter(pk=self.user.pk).exists())

    def test_flagged_agent_cannot_discover_contactout_mcp_tools(self):
        self.flag.users.add(self.user)
        contactout_tool = MCPToolInfo(
            "contactout-config",
            "mcp_contactout_search_people",
            "contactout",
            "search_people",
            "Search ContactOut through MCP",
            {},
        )
        pipedream_contactout_tool = MCPToolInfo(
            "pipedream-config",
            "contactout-search-people",
            "pipedream",
            "contactout-search-people",
            "Search ContactOut through Pipedream",
            {},
            app_slug="contactout",
        )
        unrelated_tool = MCPToolInfo(
            "other-config",
            "mcp_other_search_people",
            "other",
            "search_people",
            "Search another provider",
            {},
        )

        visible = filter_contactout_mcp_tools_for_agent(
            self.agent,
            [contactout_tool, pipedream_contactout_tool, unrelated_tool],
        )

        self.assertEqual(visible, [unrelated_tool])
        self.flag.users.remove(self.user)
        self.assertEqual(
            filter_contactout_mcp_tools_for_agent(
                self.agent,
                [contactout_tool, pipedream_contactout_tool, unrelated_tool],
            ),
            [contactout_tool, pipedream_contactout_tool, unrelated_tool],
        )

    @patch("api.agent.tools.tool_manager.execute_mcp_tool")
    def test_flagged_agent_cannot_execute_contactout_through_generic_pipedream_tool(self, mock_execute):
        self.flag.users.add(self.user)
        entry = ToolCatalogEntry(
            provider="mcp",
            full_name="configure_component",
            description="Configure a Pipedream component",
            parameters={},
            tool_server="pipedream",
            tool_name="configure_component",
        )

        result = execute_enabled_tool(
            self.agent,
            entry.full_name,
            {"componentKey": "contactout-search-people"},
            resolved_entry=entry,
        )

        self.assertEqual(result["error_code"], CONTACTOUT_MCP_BLOCKED)
        self.assertEqual(result["replacement"], CONTACTOUT_TOOL_NAME)
        self.assertFalse(result["retryable"])
        mock_execute.assert_not_called()

    def test_mcp_manager_blocks_pre_resolved_contactout_tool(self):
        self.flag.users.add(self.user)
        tool_info = MCPToolInfo(
            "contactout-config",
            "mcp_contactout_search_people",
            "contactout",
            "search_people",
            "Search ContactOut through MCP",
            {},
        )

        result = MCPToolManager().execute_mcp_tool(
            self.agent,
            tool_info.full_name,
            {},
            tool_info=tool_info,
        )

        self.assertEqual(result["error_code"], CONTACTOUT_MCP_BLOCKED)

    def test_flagged_agent_enabled_roster_omits_stale_contactout_mcp_row(self):
        self.flag.users.add(self.user)
        server_config = MCPServerConfig.objects.create(
            scope=MCPServerConfig.Scope.PLATFORM,
            name="contactout",
            display_name="ContactOut MCP",
            description="",
            url="https://example.com/contactout-mcp",
        )
        tool_info = MCPToolInfo(
            str(server_config.id),
            "mcp_contactout_search_people",
            "contactout",
            "search_people",
            "Search ContactOut through MCP",
            {"type": "object", "properties": {}},
        )
        PersistentAgentEnabledTool.objects.create(
            agent=self.agent,
            tool_full_name=tool_info.full_name,
            tool_server=tool_info.server_name,
            tool_name=tool_info.tool_name,
            server_config=server_config,
        )
        manager = MCPToolManager()

        with patch.object(manager, "get_tools_for_agent", return_value=[tool_info]):
            definitions = manager.get_enabled_tools_definitions(self.agent)

        self.assertEqual(definitions, [])

    @patch("api.agent.tools.contactout.requests.post")
    def test_removing_flag_hides_stale_enabled_row_and_blocks_direct_execution(self, mock_post):
        self.flag.users.add(self.user)
        ensure_default_tools_enabled(self.agent)
        self.flag.users.remove(self.user)

        definitions = get_enabled_tool_definitions(self.agent)
        direct_result = execute_contactout(
            self.agent,
            {"operation": SEARCH_PEOPLE, "people_filters": {}},
        )

        self.assertNotIn(CONTACTOUT_TOOL_NAME, [item["function"]["name"] for item in definitions])
        self.assertEqual(direct_result["status"], "error")
        self.assertIn("not enabled", direct_result["message"])
        mock_post.assert_not_called()

    @patch("api.agent.tools.contactout.requests.post")
    def test_enabled_tool_routes_through_native_contactout_executor(self, mock_post):
        self.flag.users.add(self.user)
        ensure_default_tools_enabled(self.agent)
        mock_post.return_value = _response({"status_code": 200, "total_results": 8})
        entry = get_available_builtin_tool_entries(self.agent)[CONTACTOUT_TOOL_NAME]

        result = execute_enabled_tool(
            self.agent,
            CONTACTOUT_TOOL_NAME,
            {"operation": COUNT_PEOPLE, "people_filters": {"company": ["Gobii"]}},
            resolved_entry=entry,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["content"]["total_results"], 8)
