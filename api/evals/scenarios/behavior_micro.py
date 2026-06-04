from copy import deepcopy
from dataclasses import dataclass, field
import json

from api.agent.comms.human_input_requests import dismiss_human_input_request
from api.agent.core.processing_flags import get_human_inbound_generation
from api.agent.tools.eval_synthetic_tools import (
    EVAL_SYNTHETIC_TOOL_DEFINITIONS,
    EVAL_SYNTHETIC_TOOL_SERVER,
    is_eval_synthetic_tool_name,
)
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry, register_scenario
from api.evals.stop_policy import (
    sqlite_batch_is_only_eval_bookkeeping_read,
    sqlite_batch_is_only_planning_state_read,
    sqlite_batch_is_only_planning_state_mutation,
    sqlite_batch_mutates_agent_config_field,
    sqlite_batch_mutates_planning_state,
)
from api.models import (
    EvalRunTask,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentCompletion,
    PersistentAgentConversation,
    PersistentAgentEnabledTool,
    PersistentAgentHumanInputRequest,
    PersistentAgentKanbanCard,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    build_web_agent_address,
    build_web_user_address,
)

PLANNING_FIRST_TURN_ASKS_BOUNDED_QUESTIONS = "planning_first_turn_asks_bounded_questions"
PLANNING_CLEAR_TASK_ENDS_PLANNING_FIRST = "planning_clear_task_ends_planning_first"
PLANNING_EXECUTE_REQUEST_STAYS_IN_PLANNING = "planning_execute_request_stays_in_planning"
PLANNING_ONE_OFF_RESEARCH_REPORT_ENDS_PLANNING_FIRST = "planning_one_off_research_report_ends_planning_first"
PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES = "planning_no_direct_schedule_or_config_updates"
PLANNING_DISMISS_AFTER_GREETING_DOES_NOT_RESUME = "planning_dismiss_after_greeting_does_not_resume"
PLANNING_FINAL_REPORT_COMPLETES_VISIBLE_PLAN = "planning_final_report_completes_visible_plan"
PLANNING_INTEGRATION_SETUP_SEARCHES_BEFORE_QUESTION = "planning_integration_setup_searches_before_question"
CHARTER_ADDS_DURABLE_PREFERENCE_PRESERVING_EXISTING = "charter_adds_durable_preference_preserving_existing"
CHARTER_ADDS_INFERRED_PREFERENCE_PRESERVING_EXISTING = "charter_adds_inferred_preference_preserving_existing"
CHARTER_EXPANDS_SPARSE_CHARTER_WITH_DETAIL = "charter_expands_sparse_charter_with_detail"
CHARTER_NARROWS_SCOPE_PRESERVING_UNRELATED_GUIDANCE = "charter_narrows_scope_preserving_unrelated_guidance"
CHARTER_IGNORES_ONE_OFF_PREFERENCE = "charter_ignores_one_off_preference"

TOOL_CHOICE_EXACT_JSON_URL_USES_HTTP_REQUEST = "tool_choice_exact_json_url_uses_http_request"
TOOL_CHOICE_CSV_DELIVERABLE_USES_CREATE_CSV = "tool_choice_csv_deliverable_uses_create_csv"
TOOL_CHOICE_PDF_DELIVERABLE_USES_CREATE_PDF = "tool_choice_pdf_deliverable_uses_create_pdf"
TOOL_CHOICE_MISSING_RECIPIENT_USES_HUMAN_INPUT = "tool_choice_missing_recipient_uses_human_input"

UPDATE_PLAN_TOOL_NAME = "update_plan"
UPDATE_PLAN_POLICY_EXPECT = "expect"
UPDATE_PLAN_POLICY_OPTIONAL = "optional"
UPDATE_PLAN_POLICIES = {
    UPDATE_PLAN_POLICY_EXPECT,
    UPDATE_PLAN_POLICY_OPTIONAL,
}


@dataclass(frozen=True)
class CommonUseCaseEvalDefinition:
    """Normalized shape for deterministic tool-choice eval cases."""

    slug: str
    category: str
    prompt: str
    expected_tools: tuple[str, ...]
    plan_expected: bool
    forbidden_tools: tuple[str, ...] = field(default_factory=tuple)
    expected_params: dict[str, object] = field(default_factory=dict)
    allowed_preamble_tools: tuple[str, ...] = field(default_factory=tuple)
    ignored_tools: tuple[str, ...] = field(default_factory=tuple)
    accepted_tool_alternatives: dict[str, tuple[str, ...]] = field(default_factory=dict)
    eval_synthetic_tools: tuple[str, ...] = field(default_factory=tuple)
    stop_after_success: bool = True

    @classmethod
    def from_mapping(cls, value):
        if "plan_expected" not in value:
            raise ValueError(f"{value.get('slug') or 'Common use case eval'} is missing plan_expected.")
        plan_expected = value.get("plan_expected")
        if not isinstance(plan_expected, bool):
            raise ValueError(f"{value.get('slug') or 'Common use case eval'} plan_expected must be a boolean.")

        stop_after_success = value.get("stop_after_success", True)
        if not isinstance(stop_after_success, bool):
            raise ValueError(f"{value.get('slug') or 'Common use case eval'} stop_after_success must be a boolean.")

        raw_alternatives = value.get("accepted_tool_alternatives") or {}
        accepted_tool_alternatives = {
            str(tool_name): tuple(alternatives or ())
            for tool_name, alternatives in raw_alternatives.items()
        }

        definition = cls(
            slug=str(value.get("slug") or "").strip(),
            category=str(value.get("category") or "").strip(),
            prompt=str(value.get("prompt") or "").strip(),
            expected_tools=tuple(value.get("expected_tools") or ()),
            plan_expected=plan_expected,
            forbidden_tools=tuple(value.get("forbidden_tools") or ()),
            expected_params=dict(value.get("expected_params") or {}),
            allowed_preamble_tools=tuple(value.get("allowed_preamble_tools") or ()),
            ignored_tools=tuple(value.get("ignored_tools") or ()),
            accepted_tool_alternatives=accepted_tool_alternatives,
            eval_synthetic_tools=tuple(value.get("eval_synthetic_tools") or ()),
            stop_after_success=stop_after_success,
        )
        definition.validate()
        return definition

    @property
    def update_plan_policy(self):
        return UPDATE_PLAN_POLICY_EXPECT if self.plan_expected else UPDATE_PLAN_POLICY_OPTIONAL

    def validate(self):
        if not self.slug:
            raise ValueError("Common use case eval is missing slug.")
        if not self.category:
            raise ValueError(f"{self.slug} is missing category.")
        if not self.prompt:
            raise ValueError(f"{self.slug} is missing prompt.")
        if not self.expected_tools:
            raise ValueError(f"{self.slug} must declare expected_tools.")
        if self.expected_params and len(self.expected_tools) != 1:
            raise ValueError(f"{self.slug} expected_params is only supported for single-tool evals.")
        if UPDATE_PLAN_TOOL_NAME in self.expected_tools or UPDATE_PLAN_TOOL_NAME in self.forbidden_tools:
            raise ValueError(f"{self.slug} must use plan_expected instead of tool lists for update_plan.")
        unknown_synthetic_tools = [
            tool_name for tool_name in self.eval_synthetic_tools
            if tool_name not in EVAL_SYNTHETIC_TOOL_DEFINITIONS
        ]
        if unknown_synthetic_tools:
            raise ValueError(f"{self.slug} declares unknown eval_synthetic_tools: {unknown_synthetic_tools}.")

    def expected_tool_names(self):
        return list(self.expected_tools)

    def forbidden_tool_names(self):
        return list(self.forbidden_tools)

    def ignored_tool_names(self):
        return list(dict.fromkeys([*IGNORED_FIRST_ACTION_TOOL_NAMES, *self.ignored_tools]))

    def allowed_preamble_tool_names(self):
        category_defaults = []
        if self.category == "sheets":
            category_defaults = GOOGLE_SHEETS_PREAMBLE_TOOLS
        elif self.category == "files":
            category_defaults = FILE_DELIVERABLE_PREAMBLE_TOOLS
        elif self.category == "monitoring":
            category_defaults = MONITORING_SETUP_PREAMBLE_TOOLS
        return list(dict.fromkeys([*IGNORED_FIRST_ACTION_TOOL_NAMES, *category_defaults, *self.allowed_preamble_tools]))

    def expected_tool_alternatives(self, tool_name):
        return list(self.accepted_tool_alternatives.get(tool_name, ()))

    def accepted_tool_names_for_expected_tool(self, tool_name):
        return [tool_name, *self.expected_tool_alternatives(tool_name)]


LINKEDIN_DISCOVERY_PREAMBLE_TOOLS = [
    "search_tools",
    "mcp_brightdata_search_engine",
    "mcp_brightdata_web_data_linkedin_company_profile",
]
LINKEDIN_POSTS_PREAMBLE_TOOLS = [
    *LINKEDIN_DISCOVERY_PREAMBLE_TOOLS,
    "mcp_brightdata_web_data_linkedin_company_profile",
    "sqlite_batch",
]
GOOGLE_SHEETS_PREAMBLE_TOOLS = [
    "search_tools",
    "google_sheets-get-spreadsheet-by-id",
    "google_sheets-get-spreadsheet-info",
    "google_sheets-list-worksheets",
    "google_sheets-get-current-user",
    "google_sheets-get-values-in-range",
    "google_sheets-read-rows",
    "google_sheets-find-row",
]
FILE_DELIVERABLE_PREAMBLE_TOOLS = ["sqlite_batch", "search_tools"]
MONITORING_SETUP_PREAMBLE_TOOLS = ["search_tools", "http_request", "mcp_brightdata_search_engine"]

COMMON_USE_CASE_RAW_EVAL_CASES = [
    {"slug": "common_use_case_001_fetch_inventory_json", "category": "api_lookup", "prompt": "Fetch https://api.example.test/inventory/widget-123.json and report inventory_count.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "expected_params": {"url": "https://api.example.test/inventory/widget-123.json"}, "plan_expected": False},
    {"slug": "common_use_case_002_fetch_status_json", "category": "api_lookup", "prompt": "Fetch https://status.example.test/api/summary.json and report the status field.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "expected_params": {"url": "https://status.example.test/api/summary.json"}, "plan_expected": False},
    {"slug": "common_use_case_003_fetch_price_json", "category": "api_lookup", "prompt": "Fetch https://prices.example.test/api/btc-usd.json and report usd_price.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "expected_params": {"url": "https://prices.example.test/api/btc-usd.json"}, "plan_expected": False},
    {"slug": "common_use_case_004_fetch_weather_json", "category": "api_lookup", "prompt": "Fetch https://weather.example.test/current?city=Boston and report temp_f.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "expected_params": {"url": "https://weather.example.test/current?city=Boston"}, "plan_expected": False},
    {"slug": "common_use_case_005_fetch_events_json", "category": "api_lookup", "prompt": "Fetch https://events.example.test/api/upcoming.json and report the first event name.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "expected_params": {"url": "https://events.example.test/api/upcoming.json"}, "plan_expected": False},
    {"slug": "common_use_case_006_fetch_release_json", "category": "api_lookup", "prompt": "Fetch https://releases.example.test/latest.json and report version.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "expected_params": {"url": "https://releases.example.test/latest.json"}, "plan_expected": False},
    {"slug": "common_use_case_007_fetch_jobs_json", "category": "api_lookup", "prompt": "Fetch https://jobs.example.test/feed.json and report open_roles.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "expected_params": {"url": "https://jobs.example.test/feed.json"}, "plan_expected": False},
    {"slug": "common_use_case_008_fetch_alerts_json", "category": "api_lookup", "prompt": "Fetch https://alerts.example.test/warnings.json and report alert_count.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "expected_params": {"url": "https://alerts.example.test/warnings.json"}, "plan_expected": False},
    {"slug": "common_use_case_009_fetch_finance_json", "category": "api_lookup", "prompt": "Fetch https://finance.example.test/api/quote/TSLA.json and report last_price.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "expected_params": {"url": "https://finance.example.test/api/quote/TSLA.json"}, "plan_expected": False},
    {"slug": "common_use_case_010_fetch_form_json", "category": "api_lookup", "prompt": "Fetch https://permits.example.test/forms/zoning.json and report required_forms.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "expected_params": {"url": "https://permits.example.test/forms/zoning.json"}, "plan_expected": False},
    {"slug": "common_use_case_011_research_competitor_pricing", "category": "web_research", "prompt": "Search the web for Acme CRM pricing changes and summarize the top result.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_012_research_ai_tools", "category": "web_research", "prompt": "Search the web for current AI meeting note tools and list three names.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_013_research_market_news", "category": "web_research", "prompt": "Search the web for recent warehouse robotics funding news and cite one source.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_014_research_regulation", "category": "web_research", "prompt": "Search the web for the latest California privacy rule update and summarize it.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_015_research_vendor_reviews", "category": "web_research", "prompt": "Search the web for reviews of ExamplePay and return one positive and one negative theme.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_016_search_remote_jobs", "category": "web_research", "prompt": "Search the web for remote senior Django engineer jobs and return three company names.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_017_search_local_events", "category": "web_research", "prompt": "Search the web for upcoming data science meetups in Austin and return two dates.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_018_search_product_launches", "category": "web_research", "prompt": "Search the web for new product launches from Contoso Health and summarize one.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_019_search_public_filings", "category": "web_research", "prompt": "Search the web for ExampleCo SEC enforcement press releases and return one link.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_020_search_reddit_mentions", "category": "web_research", "prompt": "Search the web for Reddit mentions of BiomeBoost Pro gut health supplement and summarize sentiment.", "expected_tools": ["mcp_brightdata_web_data_reddit_posts"], "forbidden_tools": ["spawn_web_task"], "accepted_tool_alternatives": {"mcp_brightdata_web_data_reddit_posts": ["mcp_brightdata_search_engine"]}, "plan_expected": False, "stop_after_success": False},
    {"slug": "common_use_case_021_scrape_known_article", "category": "web_scrape", "prompt": "Scrape https://news.example.test/article-42 and return the headline.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_022_scrape_known_blog", "category": "web_scrape", "prompt": "Scrape https://blog.example.test/q2-roadmap and return the author name.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_023_scrape_known_pricing_page", "category": "web_scrape", "prompt": "Scrape https://vendor.example.test/pricing and return the starter plan price.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_024_scrape_known_docs_page", "category": "web_scrape", "prompt": "Scrape https://docs.example.test/api/auth and return the required header name.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_025_scrape_known_permit_page", "category": "web_scrape", "prompt": "Scrape https://borough.example.test/permits/zoning and return the filing fee.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_026_scrape_known_jobs_page", "category": "web_scrape", "prompt": "Scrape https://careers.example.test/jobs and return the first listed role.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_027_scrape_known_changelog", "category": "web_scrape", "prompt": "Scrape https://app.example.test/changelog and return the latest release date.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "accepted_tool_alternatives": {"mcp_brightdata_scrape_as_markdown": ["http_request"]}, "plan_expected": False},
    {"slug": "common_use_case_028_scrape_known_directory", "category": "web_scrape", "prompt": "Scrape https://directory.example.test/vendors and return the first vendor name.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_029_scrape_known_support_page", "category": "web_scrape", "prompt": "Scrape https://support.example.test/status and return the support email.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_030_scrape_known_event_page", "category": "web_scrape", "prompt": "Scrape https://events.example.test/summit and return the venue.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_031_linkedin_person_profile", "category": "lead_sourcing", "prompt": "Find the LinkedIn profile for Jordan Lee at Acme AI and return title and location.", "expected_tools": ["mcp_brightdata_web_data_linkedin_person_profile"], "forbidden_tools": ["spawn_web_task"], "allowed_preamble_tools": LINKEDIN_DISCOVERY_PREAMBLE_TOOLS, "accepted_tool_alternatives": {"mcp_brightdata_web_data_linkedin_person_profile": ["mcp_brightdata_web_data_linkedin_people_search"]}, "plan_expected": False},
    {"slug": "common_use_case_032_linkedin_company_profile", "category": "lead_sourcing", "prompt": "Look up the LinkedIn company profile for Acme AI and return industry and size.", "expected_tools": ["mcp_brightdata_web_data_linkedin_company_profile"], "forbidden_tools": ["spawn_web_task"], "allowed_preamble_tools": LINKEDIN_DISCOVERY_PREAMBLE_TOOLS, "plan_expected": False},
    {"slug": "common_use_case_033_linkedin_job_listings", "category": "lead_sourcing", "prompt": "Find LinkedIn job listings for Acme AI and return two open role titles.", "expected_tools": ["mcp_brightdata_web_data_linkedin_job_listings"], "forbidden_tools": ["spawn_web_task"], "allowed_preamble_tools": LINKEDIN_DISCOVERY_PREAMBLE_TOOLS, "plan_expected": False},
    {"slug": "common_use_case_034_linkedin_people_search", "category": "lead_sourcing", "prompt": "Search LinkedIn for product leaders at Acme AI and return three names.", "expected_tools": ["mcp_brightdata_web_data_linkedin_people_search"], "forbidden_tools": ["spawn_web_task"], "allowed_preamble_tools": LINKEDIN_DISCOVERY_PREAMBLE_TOOLS, "plan_expected": False},
    {"slug": "common_use_case_035_linkedin_posts", "category": "lead_sourcing", "prompt": "Find recent LinkedIn posts from Acme AI and summarize the latest post.", "expected_tools": ["mcp_brightdata_web_data_linkedin_posts"], "forbidden_tools": ["spawn_web_task"], "allowed_preamble_tools": LINKEDIN_POSTS_PREAMBLE_TOOLS, "plan_expected": False},
    {"slug": "common_use_case_036_apollo_contacts", "category": "lead_sourcing", "prompt": "Search Apollo for VP Sales contacts at healthcare SaaS companies in Boston.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "allowed_preamble_tools": ["search_tools", "enable_system_skills"], "accepted_tool_alternatives": {"http_request": ["apollo_io-search-contacts"]}, "eval_synthetic_tools": ["apollo_io-search-contacts"], "plan_expected": False},
    {"slug": "common_use_case_037_apollo_accounts", "category": "lead_sourcing", "prompt": "Search Apollo for cybersecurity accounts with 50-200 employees in Austin.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "allowed_preamble_tools": ["search_tools", "enable_system_skills"], "accepted_tool_alternatives": {"http_request": ["apollo_io-search-accounts"]}, "eval_synthetic_tools": ["apollo_io-search-accounts"], "plan_expected": False},
    {"slug": "common_use_case_038_apollo_enrich_person", "category": "lead_sourcing", "prompt": "Enrich the Apollo profile for pat@example.test and return company and title.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "allowed_preamble_tools": ["search_tools", "enable_system_skills"], "accepted_tool_alternatives": {"http_request": ["apollo_io-people-enrichment"]}, "eval_synthetic_tools": ["apollo_io-people-enrichment"], "plan_expected": False},
    {"slug": "common_use_case_039_amazon_product", "category": "commerce_research", "prompt": "Get Amazon product data for ASIN B000TEST01 and return rating and price.", "expected_tools": ["mcp_brightdata_web_data_amazon_product"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_040_instagram_profile", "category": "social_research", "prompt": "Get Instagram profile data for examplebrand and return follower count.", "expected_tools": ["mcp_brightdata_web_data_instagram_profiles"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_041_reddit_posts", "category": "social_research", "prompt": "Fetch Reddit posts about ExampleApp and summarize the top complaint.", "expected_tools": ["mcp_brightdata_web_data_reddit_posts"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_042_google_maps_reviews", "category": "local_research", "prompt": "Fetch Google Maps reviews for Example Cafe and summarize the rating themes.", "expected_tools": ["mcp_brightdata_web_data_google_maps_reviews"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_043_yahoo_finance_business", "category": "finance_research", "prompt": "Fetch Yahoo Finance business data for MSFT and return market cap.", "expected_tools": ["mcp_brightdata_web_data_yahoo_finance_business"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_044_linkedin_company_jobs", "category": "lead_sourcing", "prompt": "Find LinkedIn job listings for a fintech company and return remote roles.", "expected_tools": ["mcp_brightdata_web_data_linkedin_job_listings"], "forbidden_tools": ["spawn_web_task"], "allowed_preamble_tools": LINKEDIN_DISCOVERY_PREAMBLE_TOOLS, "plan_expected": False},
    {"slug": "common_use_case_045_linkedin_candidate_search", "category": "lead_sourcing", "prompt": "Search LinkedIn for senior backend candidates in Toronto with Python experience.", "expected_tools": ["mcp_brightdata_web_data_linkedin_people_search"], "forbidden_tools": ["spawn_web_task"], "allowed_preamble_tools": LINKEDIN_DISCOVERY_PREAMBLE_TOOLS, "plan_expected": False},
    {"slug": "common_use_case_046_sheets_read_range", "category": "sheets", "prompt": "Read A1:D20 from the Leads worksheet in spreadsheet sheet-123.", "expected_tools": ["google_sheets-get-values-in-range"], "forbidden_tools": ["sqlite_batch"], "accepted_tool_alternatives": {"google_sheets-get-values-in-range": ["google_sheets-read-rows"]}, "plan_expected": False},
    {"slug": "common_use_case_047_sheets_find_row", "category": "sheets", "prompt": "Find the row in spreadsheet sheet-123 where email equals ana@example.test.", "expected_tools": ["google_sheets-find-row"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_048_sheets_add_single_row", "category": "sheets", "prompt": "In spreadsheet sheet-123, add one row to the Leads worksheet: company Acme, priority high, owner Sam.", "expected_tools": ["google_sheets-add-single-row"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_050_sheets_update_cell", "category": "sheets", "prompt": "Update cell C8 in spreadsheet sheet-123 to Qualified.", "expected_tools": ["google_sheets-update-cell"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_051_sheets_update_row", "category": "sheets", "prompt": "In spreadsheet sheet-123 Leads worksheet, update the row where company is Globex so status is Contacted.", "expected_tools": ["google_sheets-update-row"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_052_sheets_update_multiple_rows", "category": "sheets", "prompt": "In spreadsheet sheet-123 Pipeline worksheet, update rows 12, 13, and 14 so follow_up_due is today.", "expected_tools": ["google_sheets-update-multiple-rows"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_053_sheets_upsert_row", "category": "sheets", "prompt": "In spreadsheet sheet-123 Accounts worksheet, upsert row keyed by domain example.test with status active.", "expected_tools": ["google_sheets-upsert-row"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_054_sheets_list_worksheets", "category": "sheets", "prompt": "List worksheets in spreadsheet sheet-123 and return their titles.", "expected_tools": ["google_sheets-list-worksheets"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_055_sheets_info", "category": "sheets", "prompt": "Get spreadsheet info for sheet-123 and report the spreadsheet title.", "expected_tools": ["google_sheets-get-spreadsheet-info"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_056_sheets_create_spreadsheet", "category": "sheets", "prompt": "Create a spreadsheet named Q2 Lead Tracker with a Leads worksheet.", "expected_tools": ["google_sheets-create-spreadsheet"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_057_sheets_read_rows", "category": "sheets", "prompt": "Read the first 10 rows from the Tasks worksheet in spreadsheet sheet-123.", "expected_tools": ["google_sheets-read-rows"], "forbidden_tools": ["sqlite_batch"], "accepted_tool_alternatives": {"google_sheets-read-rows": ["google_sheets-get-values-in-range"]}, "plan_expected": False},
    {"slug": "common_use_case_058_sheets_get_by_id", "category": "sheets", "prompt": "Open spreadsheet sheet-123 by id and return its name.", "expected_tools": ["google_sheets-get-spreadsheet-by-id"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_059_sheets_current_user", "category": "sheets", "prompt": "Check the connected Google Sheets user before editing the tracker.", "expected_tools": ["google_sheets-get-current-user"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_060_sheets_append_rows", "category": "sheets", "prompt": "Append two new rows to the Research worksheet in spreadsheet sheet-123: company Vanta priority high owner Maya; company Notion priority medium owner Omar.", "expected_tools": ["google_sheets-add-rows"], "forbidden_tools": ["sqlite_batch"], "accepted_tool_alternatives": {"google_sheets-add-rows": ["google_sheets-add-multiple-rows"]}, "plan_expected": False},
    {"slug": "common_use_case_061_send_summary_email", "category": "outbound", "prompt": "Email ana@example.test three sentences: Enterprise leads increased. Acme renewal moved to legal review. Globex needs a Friday follow-up.", "expected_tools": ["send_email"], "forbidden_tools": ["send_sms"], "accepted_tool_alternatives": {"send_email": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_062_send_attachment_email", "category": "outbound", "prompt": "Email pat@example.test that the prepared report is attached at $[/exports/report.pdf].", "expected_tools": ["send_email"], "forbidden_tools": ["send_sms"], "allowed_preamble_tools": ["sqlite_batch", "read_file"], "accepted_tool_alternatives": {"send_email": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_063_send_followup_email", "category": "outbound", "prompt": "Send a polite follow-up email to lee@example.test about the demo times.", "expected_tools": ["send_email"], "forbidden_tools": ["send_sms"], "accepted_tool_alternatives": {"send_email": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_064_send_digest_email", "category": "outbound", "prompt": "Email ops@example.test the daily incident digest with bullets: API latency stayed under 120 ms; queue backlog cleared by 14:00 UTC; no Sev1 incidents opened.", "expected_tools": ["send_email"], "forbidden_tools": ["send_sms"], "allowed_preamble_tools": ["search_tools"], "accepted_tool_alternatives": {"send_email": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_065_send_status_sms", "category": "outbound", "prompt": "Text +15555550123 that the build finished successfully.", "expected_tools": ["send_sms"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"send_sms": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_066_send_meeting_sms", "category": "outbound", "prompt": "Send an SMS to +15555550123 saying the meeting moved to 3pm.", "expected_tools": ["send_sms"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"send_sms": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_067_request_contact_email_permission", "category": "outbound", "prompt": "Ask permission to email new-contact@example.test about the partnership intro.", "expected_tools": ["request_contact_permission"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_068_request_sms_permission", "category": "outbound", "prompt": "Create a contact-permission request for SMS +15555550123 so you can text that number about the urgent outage after approval.", "expected_tools": ["request_contact_permission"], "forbidden_tools": ["send_sms"], "plan_expected": False},
    {"slug": "common_use_case_069_secure_api_key_request", "category": "credentials", "prompt": "Request the missing STRIPE_API_KEY secret so you can call the Stripe API.", "expected_tools": ["secure_credentials_request"], "allowed_preamble_tools": ["send_chat_message"], "plan_expected": False},
    {"slug": "common_use_case_070_secure_login_request", "category": "credentials", "prompt": "Create a secure credential request for the portal password for https://vendor.example.test before logging in.", "expected_tools": ["secure_credentials_request"], "allowed_preamble_tools": ["send_chat_message"], "plan_expected": False},
    {"slug": "common_use_case_071_create_leads_csv", "category": "files", "prompt": "Create /exports/leads.csv with columns company,email,priority and two rows.", "expected_tools": ["create_csv"], "forbidden_tools": ["create_file"], "plan_expected": False},
    {"slug": "common_use_case_072_create_jobs_csv", "category": "files", "prompt": "Create /exports/jobs.csv with columns title,company,url and three rows.", "expected_tools": ["create_csv"], "forbidden_tools": ["create_file"], "plan_expected": False},
    {"slug": "common_use_case_073_create_status_pdf", "category": "files", "prompt": "Create /exports/status.pdf as a one-page PDF. Include wins: beta launched, onboarding -18%; risks: vendor delay, SOC2 evidence; next steps: pilot outreach, security review.", "expected_tools": ["create_pdf"], "forbidden_tools": ["create_file"], "plan_expected": False},
    {"slug": "common_use_case_074_create_permit_pdf", "category": "files", "prompt": "Create /exports/permit-summary.pdf as a PDF. Include: decks over 30 inches need a building permit; exterior decks need zoning review; site plan required; base fee is 50 dollars.", "expected_tools": ["create_pdf"], "forbidden_tools": ["create_file"], "plan_expected": False},
    {"slug": "common_use_case_075_create_markdown_file", "category": "files", "prompt": "Create /exports/notes.md. Summary: roadmap review covered beta launch, onboarding improvements, vendor delay risk. Action items: Sam pilot outreach; Priya SOC2 evidence.", "expected_tools": ["create_file"], "forbidden_tools": ["create_csv", "create_pdf"], "plan_expected": False},
    {"slug": "common_use_case_076_create_json_file", "category": "files", "prompt": "Create /exports/config.json containing feature_enabled true and retry_count 3.", "expected_tools": ["create_file"], "forbidden_tools": ["create_csv", "create_pdf"], "plan_expected": False},
    {"slug": "common_use_case_077_create_bar_chart", "category": "files", "prompt": "Create a bar chart of weekly leads with values 12, 18, 9, and 24.", "expected_tools": ["create_chart"], "forbidden_tools": ["create_csv"], "allowed_preamble_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_078_create_line_chart", "category": "files", "prompt": "Create a line chart for daily signups with values 4, 7, 5, 11, and 13.", "expected_tools": ["create_chart"], "forbidden_tools": ["create_csv"], "plan_expected": False},
    {"slug": "common_use_case_079_create_report_with_chart", "category": "files", "prompt": "Create a chart showing revenue by month from Jan 120, Feb 135, Mar 150, Apr 142, May 165, Jun 180 and prepare it for a PDF report.", "expected_tools": ["create_chart"], "forbidden_tools": ["send_email"], "allowed_preamble_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_080_read_uploaded_file", "category": "files", "prompt": "Read /uploads/brief.txt and summarize the three requested edits.", "expected_tools": ["read_file"], "forbidden_tools": ["mcp_brightdata_search_engine"], "plan_expected": False},
    {"slug": "common_use_case_081_sqlite_create_table", "category": "database", "prompt": "Create a SQLite table leads with columns company, email, and priority.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-add-single-row"], "plan_expected": False},
    {"slug": "common_use_case_082_sqlite_insert_rows", "category": "database", "prompt": "Insert two lead rows into the SQLite leads table.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-add-single-row"], "plan_expected": False},
    {"slug": "common_use_case_083_sqlite_query_counts", "category": "database", "prompt": "Query SQLite for lead counts grouped by priority.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-get-values-in-range"], "plan_expected": False},
    {"slug": "common_use_case_084_sqlite_update_status", "category": "database", "prompt": "Update SQLite lead Acme to status contacted.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-update-row"], "plan_expected": False},
    {"slug": "common_use_case_085_sqlite_join_tables", "category": "database", "prompt": "The SQLite database already has accounts and contacts tables. Run a join query by account_id and summarize the rows.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-get-values-in-range"], "plan_expected": False},
    {"slug": "common_use_case_086_sqlite_export_query_csv", "category": "database", "prompt": "Run a SQLite query for open leads, then create a CSV export.", "expected_tools": ["sqlite_batch", "create_csv"], "forbidden_tools": ["google_sheets-get-values-in-range"], "plan_expected": False},
    {"slug": "common_use_case_087_sqlite_clean_duplicates", "category": "database", "prompt": "Remove duplicate emails from the SQLite contacts table.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-update-multiple-rows"], "plan_expected": False},
    {"slug": "common_use_case_088_sqlite_add_index", "category": "database", "prompt": "Add a SQLite index on contacts email for faster lookup.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-update-cell"], "plan_expected": False},
    {"slug": "common_use_case_089_enable_database", "category": "database", "prompt": "Enable the database so you can store a lead tracker for this agent.", "expected_tools": ["enable_database"], "forbidden_tools": ["google_sheets-create-spreadsheet"], "accepted_tool_alternatives": {"enable_database": ["sqlite_batch"]}, "plan_expected": False},
    {"slug": "common_use_case_090_sqlite_summarize_messages", "category": "database", "prompt": "Query SQLite message history and summarize the last five user requests.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["mcp_brightdata_search_engine"], "plan_expected": False},
    {"slug": "common_use_case_091_schedule_daily_digest", "category": "monitoring", "prompt": "Set a daily 9am ET schedule for a competitor pricing digest.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_092_schedule_hourly_monitor", "category": "monitoring", "prompt": "Set an hourly schedule to monitor https://status.example.test/support and report support status changes.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_093_schedule_weekly_report", "category": "monitoring", "prompt": "Set a Monday 8am ET schedule for a weekly pipeline report.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_094_update_agent_charter", "category": "monitoring", "prompt": "Update your charter to monitor AI funding news and summarize notable deals.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_095_request_research_scope", "category": "human_input", "prompt": "Ask me which target account segment to research before starting the work.", "expected_tools": ["request_human_input"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_096_schedule_price_alert", "category": "monitoring", "prompt": "Set a daily schedule to check the BTC-USD price and alert only if it moves 5 percent.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_097_schedule_permit_check", "category": "monitoring", "prompt": "Set a weekday schedule to check https://borough.example.test/permits/decks for permit page updates.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_098_update_charter_sourcing", "category": "monitoring", "prompt": "Update your charter to source three qualified backend candidates each weekday.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_099_request_monitoring_scope", "category": "human_input", "prompt": "Ask which competitors and update types matter before setting up monitoring.", "expected_tools": ["request_human_input"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_100_schedule_daily_email_digest", "category": "monitoring", "prompt": "Set a daily 7am ET schedule to prepare a concise email digest of market news for ops@example.test; do not send the first digest now.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_101_linkedin_revenue_ops_candidates", "category": "lead_sourcing", "prompt": "Search LinkedIn for senior revenue operations candidates in Chicago with Salesforce experience; return five names with profile URLs.", "expected_tools": ["mcp_brightdata_web_data_linkedin_people_search"], "forbidden_tools": ["spawn_web_task"], "allowed_preamble_tools": LINKEDIN_DISCOVERY_PREAMBLE_TOOLS, "plan_expected": False},
    {"slug": "common_use_case_102_linkedin_hr_leaders", "category": "lead_sourcing", "prompt": "Find HR leaders at Series B fintech companies in New York using LinkedIn people search; return title, company, and location.", "expected_tools": ["mcp_brightdata_web_data_linkedin_people_search"], "forbidden_tools": ["spawn_web_task"], "allowed_preamble_tools": LINKEDIN_DISCOVERY_PREAMBLE_TOOLS, "plan_expected": False},
    {"slug": "common_use_case_103_apollo_logistics_leads", "category": "lead_sourcing", "prompt": "Search Apollo for operations leaders at logistics companies with 200-1000 employees in Texas; dedupe contacts before reporting.", "expected_tools": ["http_request"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "allowed_preamble_tools": ["search_tools", "enable_system_skills"], "accepted_tool_alternatives": {"http_request": ["apollo_io-search-contacts"]}, "eval_synthetic_tools": ["apollo_io-search-contacts"], "plan_expected": False},
    {"slug": "common_use_case_104_recent_vc_funding_research", "category": "web_research", "prompt": "Search the web for recent VC funding rounds in warehouse robotics and cite two current sources.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_105_current_finance_snapshot", "category": "finance_research", "prompt": "Fetch Yahoo Finance business data for NVDA and return current price, percent change, and market timestamp.", "expected_tools": ["mcp_brightdata_web_data_yahoo_finance_business"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_106_maps_dental_lead_screen", "category": "local_research", "prompt": "Use Google Maps reviews to qualify local dental practices with scheduling complaints; return review evidence.", "expected_tools": ["mcp_brightdata_web_data_google_maps_reviews"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_107_schedule_vc_digest", "category": "monitoring", "prompt": "Set a weekly Monday 8am ET VC funding digest for AI infrastructure deals; include source links and do not run it now.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_109_http_json_dedupe_domains", "category": "intelligent_work", "prompt": "Fetch https://api.example.test/vendors/alpha.json and https://api.example.test/vendors/beta.json, then use SQLite to dedupe vendors by domain and report the top score.", "expected_tools": ["http_request", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_110_scrape_compare_with_sqlite", "category": "intelligent_work", "prompt": "Scrape https://stripe.com/docs/security and https://auth0.com/docs/security, then call sqlite_batch over prior scrape results to compare claims. Do not spawn a browser task.", "expected_tools": ["mcp_brightdata_scrape_as_markdown", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_111_prior_results_sqlite_rank", "category": "intelligent_work", "prompt": "Prior pricing scrapes are in __tool_results; use one SQLite query to rank annual cost without one-result blob loops.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["read_file"], "plan_expected": False},
    {"slug": "common_use_case_112_file_json_dedupe_report", "category": "intelligent_work", "prompt": "Read /uploads/vendor-feed.json and use SQLite/json_each to dedupe companies by domain before reporting export-ready rows.", "expected_tools": ["read_file", "sqlite_batch"], "forbidden_tools": ["mcp_brightdata_search_engine"], "plan_expected": False},
    {"slug": "common_use_case_113_file_pipeline_sqlite_summary", "category": "intelligent_work", "prompt": "Read /uploads/pipeline.csv and use SQLite to group qualified pipeline by owner before reporting chart-ready rows.", "expected_tools": ["read_file", "sqlite_batch"], "forbidden_tools": ["mcp_brightdata_search_engine"], "plan_expected": False},
    {"slug": "common_use_case_115_sheets_read_sqlite_rank", "category": "sheets", "prompt": "Read sheet-123 Leads rows and use SQLite to dedupe by email before reporting the highest-priority alex@example.test row.", "expected_tools": ["google_sheets-read-rows", "sqlite_batch"], "forbidden_tools": ["request_human_input"], "accepted_tool_alternatives": {"google_sheets-read-rows": ["google_sheets-get-values-in-range"]}, "plan_expected": False},
    {"slug": "common_use_case_116_maps_default_city_reviews", "category": "local_research", "prompt": "Find dental practices with scheduling complaints; if city is omitted, assume Austin and use Google Maps reviews.", "expected_tools": ["mcp_brightdata_web_data_google_maps_reviews"], "forbidden_tools": ["request_human_input", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_117_linkedin_default_company_jobs", "category": "lead_sourcing", "prompt": "Find remote fintech backend roles on LinkedIn; if company is unspecified, use a representative fintech company.", "expected_tools": ["mcp_brightdata_web_data_linkedin_job_listings"], "forbidden_tools": ["request_human_input", "spawn_web_task"], "allowed_preamble_tools": LINKEDIN_DISCOVERY_PREAMBLE_TOOLS, "plan_expected": False},
    {"slug": "common_use_case_118_apollo_dedupe_contacts_sqlite", "category": "lead_sourcing", "prompt": "Search Apollo for RevOps leaders at Texas logistics firms, then use SQLite to dedupe contacts by email.", "expected_tools": ["http_request", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "allowed_preamble_tools": ["search_tools", "enable_system_skills"], "accepted_tool_alternatives": {"http_request": ["apollo_io-search-contacts"]}, "eval_synthetic_tools": ["apollo_io-search-contacts"], "plan_expected": False},
    {"slug": "common_use_case_119_http_nested_json_recover", "category": "intelligent_work", "prompt": "Fetch https://api.example.test/leads.json; if fields are nested/noisy, inspect with SQLite JSON functions.", "expected_tools": ["http_request", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_120_scrape_noisy_extract_sqlite", "category": "intelligent_work", "prompt": "Scrape https://vendor.example.test/reviews, then call sqlite_batch on noisy scrape text to extract unique complaint themes. Do not spawn a browser task.", "expected_tools": ["mcp_brightdata_scrape_as_markdown", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_121_sheets_direct_add_no_question", "category": "sheets", "prompt": "The tracker id is sheet-123. Add row: company Hexa, owner Lee, priority high; do not ask which sheet.", "expected_tools": ["google_sheets-add-single-row"], "forbidden_tools": ["request_human_input"], "plan_expected": False},
    {"slug": "common_use_case_122_custom_tool_bulk_api_sqlite", "category": "intelligent_work", "prompt": "Create a custom tool to page product API URLs https://api.example.test/products?page=1 and https://api.example.test/products?page=2; store normalized rows in SQLite.", "expected_tools": ["create_custom_tool"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_123_custom_tool_partial_retry", "category": "intelligent_work", "prompt": "Create a custom tool that retries partial HTTP JSON pages from https://api.example.test/events?cursor=start, writes successes to SQLite, and returns next_action.", "expected_tools": ["create_custom_tool"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_124_tool_results_cte_dedupe_urls", "category": "intelligent_work", "prompt": "Prior scrapes are in __tool_results. Use one SQLite CTE over all scrape rows to dedupe URLs and summarize claims.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["read_file"], "plan_expected": False},
    {"slug": "common_use_case_125_tool_results_json_each_plan", "category": "intelligent_work", "prompt": "Prior API results contain nested offers arrays; use sqlite_batch json_each to pick the cheapest HIPAA-ready plan.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["read_file"], "plan_expected": False},
    {"slug": "common_use_case_126_http_sqlite_weekly_trend", "category": "intelligent_work", "prompt": "Fetch https://api.example.test/signups.json, aggregate signups by week in SQLite, then report the trend.", "expected_tools": ["http_request", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_127_search_scrape_sqlite_extract", "category": "intelligent_work", "prompt": "Search for ExamplePay pricing pages, scrape the best result, then use SQLite to extract plan names.", "expected_tools": ["mcp_brightdata_search_engine", "mcp_brightdata_scrape_as_markdown", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_128_maps_reviews_sqlite_dedupe", "category": "local_research", "prompt": "Use Google Maps reviews for Austin cafes with long-wait complaints, then use SQLite to dedupe snippets by place.", "expected_tools": ["mcp_brightdata_web_data_google_maps_reviews", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_129_reddit_posts_sqlite_sentiment", "category": "social_research", "prompt": "Fetch Reddit posts about ExampleApp, then use SQLite to dedupe repeated complaints before summarizing sentiment.", "expected_tools": ["mcp_brightdata_web_data_reddit_posts", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_130_yahoo_finance_sqlite_calc", "category": "finance_research", "prompt": "Fetch Yahoo Finance business data for MSFT, then use SQLite to calculate market-cap-to-revenue if fields exist.", "expected_tools": ["mcp_brightdata_web_data_yahoo_finance_business", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_131_vendor_default_assumption", "category": "web_research", "prompt": "Find a representative customer-support AI vendor and summarize pricing; choose one if unspecified and disclose it.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["request_human_input", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_132_sheets_blank_due_bulk_update", "category": "sheets", "prompt": "Read sheet-123 Tasks, infer blank due dates as today, then update rows 12, 13, and 14 with follow_up_due.", "expected_tools": ["google_sheets-read-rows", "google_sheets-update-multiple-rows"], "forbidden_tools": ["request_human_input"], "accepted_tool_alternatives": {"google_sheets-read-rows": ["google_sheets-get-values-in-range"]}, "plan_expected": False},
    {"slug": "common_use_case_133_http_sqlite_dedupe_report", "category": "intelligent_work", "prompt": "Fetch https://api.example.test/accounts.json and use SQLite to dedupe domains before reporting export-ready rows.", "expected_tools": ["http_request", "sqlite_batch"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_134_file_support_group_report", "category": "intelligent_work", "prompt": "Read /uploads/support-dump.json and group tickets by account in SQLite before reporting counts.", "expected_tools": ["read_file", "sqlite_batch"], "forbidden_tools": ["mcp_brightdata_search_engine"], "plan_expected": False},
    {"slug": "common_use_case_135_search_scrape_two_sources", "category": "web_research", "prompt": "Search current warehouse robotics funding, scrape two strong sources, and cite both without extra query variants.", "expected_tools": ["mcp_brightdata_search_engine", "mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_136_apollo_connect_tool_search", "category": "integration_discovery", "prompt": "Connect my Apollo.io account so you can use it for lead sourcing.", "expected_tools": ["search_tools"], "forbidden_tools": ["request_human_input", "secure_credentials_request", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_137_slack_connect_tool_search", "category": "integration_discovery", "prompt": "Connect Slack so you can read and summarize customer feedback from our support channel.", "expected_tools": ["search_tools"], "forbidden_tools": ["request_human_input", "secure_credentials_request", "spawn_web_task"], "plan_expected": False},
]

COMMON_USE_CASE_EVAL_CASES = tuple(
    CommonUseCaseEvalDefinition.from_mapping(case)
    for case in COMMON_USE_CASE_RAW_EVAL_CASES
)

COMMON_USE_CASE_MICRO_SCENARIO_SLUGS = [case.slug for case in COMMON_USE_CASE_EVAL_CASES]

PLANNING_MICRO_SCENARIO_SLUGS = [
    PLANNING_FIRST_TURN_ASKS_BOUNDED_QUESTIONS,
    PLANNING_CLEAR_TASK_ENDS_PLANNING_FIRST,
    PLANNING_EXECUTE_REQUEST_STAYS_IN_PLANNING,
    PLANNING_ONE_OFF_RESEARCH_REPORT_ENDS_PLANNING_FIRST,
    PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES,
    PLANNING_DISMISS_AFTER_GREETING_DOES_NOT_RESUME,
    PLANNING_FINAL_REPORT_COMPLETES_VISIBLE_PLAN,
    PLANNING_INTEGRATION_SETUP_SEARCHES_BEFORE_QUESTION,
]

CHARTER_MEMORY_MICRO_SCENARIO_SLUGS = [
    CHARTER_ADDS_DURABLE_PREFERENCE_PRESERVING_EXISTING,
    CHARTER_ADDS_INFERRED_PREFERENCE_PRESERVING_EXISTING,
    CHARTER_EXPANDS_SPARSE_CHARTER_WITH_DETAIL,
    CHARTER_NARROWS_SCOPE_PRESERVING_UNRELATED_GUIDANCE,
    CHARTER_IGNORES_ONE_OFF_PREFERENCE,
]

TOOL_CHOICE_MICRO_SCENARIO_SLUGS = [
    TOOL_CHOICE_EXACT_JSON_URL_USES_HTTP_REQUEST,
    TOOL_CHOICE_CSV_DELIVERABLE_USES_CREATE_CSV,
    TOOL_CHOICE_PDF_DELIVERABLE_USES_CREATE_PDF,
    TOOL_CHOICE_MISSING_RECIPIENT_USES_HUMAN_INPUT,
    *COMMON_USE_CASE_MICRO_SCENARIO_SLUGS,
]

BEHAVIOR_MICRO_SCENARIO_SLUGS = (
    PLANNING_MICRO_SCENARIO_SLUGS
    + TOOL_CHOICE_MICRO_SCENARIO_SLUGS
)

SUBSTANTIVE_WORK_TOOL_NAMES = {
    "create_file",
    "create_csv",
    "create_pdf",
    "create_chart",
    "create_image",
    "create_video",
    "http_request",
    "python_exec",
    "run_command",
    "send_email",
    "send_sms",
    "send_webhook_event",
    "request_contact_permission",
    "secure_credentials_request",
    "spawn_web_task",
}

AGENT_CONFIG_MUTATION_TOOL_NAMES = {
    "update_schedule",
    "update_charter",
}

PLANNING_MUTATION_TOOL_NAMES = AGENT_CONFIG_MUTATION_TOOL_NAMES | {
    "update_plan",
}

IGNORED_FIRST_ACTION_TOOL_NAMES = {
    "send_chat_message",
    "sleep_until_next_trigger",
}

PLANNING_READ_ONLY_TOOL_NAMES = {
    "mcp_brightdata_scrape_as_markdown",
    "mcp_brightdata_search_engine",
    "read_file",
    "search_engine",
    "search_engine_batch",
    "search_tools",
}

GOOGLE_SHEETS_EVAL_SYNTHETIC_TOOL_NAMES = {
    "google_sheets-get-values-in-range",
    "google_sheets-find-row",
    "google_sheets-add-single-row",
    "google_sheets-add-multiple-rows",
    "google_sheets-update-cell",
    "google_sheets-update-row",
    "google_sheets-update-multiple-rows",
    "google_sheets-upsert-row",
    "google_sheets-list-worksheets",
    "google_sheets-get-spreadsheet-info",
    "google_sheets-create-spreadsheet",
    "google_sheets-read-rows",
    "google_sheets-get-spreadsheet-by-id",
    "google_sheets-get-current-user",
    "google_sheets-add-rows",
}

PLANNING_ALLOWED_FIRST_ACTION_TOOL_NAMES = {
    "request_human_input",
    "end_planning",
}

def get_tool_calls_for_run(run_id, *, after=None, tool_names=None):
    queryset = PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
    if after is not None:
        queryset = queryset.filter(step__created_at__gte=after)
    if tool_names is not None:
        queryset = queryset.filter(tool_name__in=list(tool_names))
    return list(queryset.select_related("step").order_by("step__created_at", "step__id"))


def get_first_relevant_tool_call(run_id, *, after=None, ignored_tool_names=None):
    ignored = set(ignored_tool_names or ())
    for call in get_tool_calls_for_run(run_id, after=after):
        if call.tool_name not in ignored:
            return call
    return None


def get_forbidden_calls_before_end_planning(run_id, *, after=None, forbidden_tool_names=None):
    forbidden = set(forbidden_tool_names or ())
    calls = []
    for call in get_tool_calls_for_run(run_id, after=after):
        if call.tool_name == "end_planning":
            break
        if call.tool_name in forbidden:
            calls.append(call)
    return calls


def tool_call_is_plan_activity(tool_call):
    return tool_call.tool_name == UPDATE_PLAN_TOOL_NAME


def get_plan_activity_calls_for_run(run_id, *, after=None):
    return [
        call
        for call in get_tool_calls_for_run(run_id, after=after)
        if tool_call_is_plan_activity(call)
    ]


def get_common_use_case_tool_calls_for_run(
    run_id,
    *,
    after=None,
    tool_names=None,
    include_sqlite_eval_bookkeeping_reads=False,
):
    return [
        call
        for call in get_tool_calls_for_run(run_id, after=after, tool_names=tool_names)
        if (include_sqlite_eval_bookkeeping_reads or not sqlite_batch_is_only_eval_bookkeeping_read(call))
        and not sqlite_batch_is_only_planning_state_read(call)
        and not sqlite_batch_is_only_planning_state_mutation(call)
    ]


def get_first_common_use_case_tool_call(run_id, *, after=None, ignored_tool_names=None):
    ignored = set(ignored_tool_names or ())
    for call in get_common_use_case_tool_calls_for_run(run_id, after=after):
        if call.tool_name not in ignored:
            return call
    return None


def get_planning_mutation_calls_before_end_planning(run_id, *, after=None):
    calls = []
    for call in get_tool_calls_for_run(run_id, after=after):
        if call.tool_name == "end_planning":
            break
        if call.tool_name in PLANNING_MUTATION_TOOL_NAMES or sqlite_batch_mutates_planning_state(call):
            calls.append(call)
    return calls


def tool_call_mutates_agent_config(tool_call):
    return (
        tool_call.tool_name in AGENT_CONFIG_MUTATION_TOOL_NAMES
        or sqlite_batch_mutates_planning_state(tool_call)
    )


def get_agent_config_mutation_calls_for_run(run_id, *, after=None):
    return [
        call
        for call in get_tool_calls_for_run(run_id, after=after)
        if tool_call_mutates_agent_config(call)
    ]


def get_pending_human_input_requests(agent_id, run_id, *, after=None):
    queryset = PersistentAgentHumanInputRequest.objects.filter(
        agent_id=agent_id,
        originating_step__eval_run_id=run_id,
        status=PersistentAgentHumanInputRequest.Status.PENDING,
    )
    if after is not None:
        queryset = queryset.filter(created_at__gte=after)
    return list(queryset.order_by("created_at", "id"))


def all_requests_have_options(requests):
    return all(isinstance(request.options_json, list) and len(request.options_json) > 0 for request in requests)


class BehaviorMicroScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "agent_behavior"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "micro")

    def _set_planning_state(self, agent_id, state):
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=state)

    def _seed_prior_processing_run(self, agent_id):
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return

        prior_step = PersistentAgentStep.objects.create(
            agent_id=agent_id,
            description="Process events",
        )
        PersistentAgentSystemStep.objects.create(
            step=prior_step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    def _seed_completed_process_run(self, agent_id):
        self._seed_prior_processing_run(agent_id)

    def _enable_builtin_tools(self, agent_id, tool_names):
        agent = PersistentAgent.objects.get(id=agent_id)
        for tool_name in tool_names:
            mark_tool_enabled_without_discovery(agent, tool_name)

    def _enable_eval_synthetic_tools(self, agent_id, tool_names):
        agent = PersistentAgent.objects.get(id=agent_id)
        for tool_name in tool_names:
            mark_tool_enabled_without_discovery(agent, tool_name)
            PersistentAgentEnabledTool.objects.filter(
                agent=agent,
                tool_full_name=tool_name,
            ).update(
                tool_server=EVAL_SYNTHETIC_TOOL_SERVER,
                tool_name=tool_name,
            )

    def _enable_sandbox_tool_visibility(self, agent_id):
        from waffle.models import Flag

        from api.services.system_settings import get_setting_definition, set_setting_value

        agent = PersistentAgent.objects.select_related("user").get(id=agent_id)
        definition = get_setting_definition("SANDBOX_COMPUTE_ENABLED")
        if definition:
            set_setting_value(definition, True)
        flag, _ = Flag.objects.get_or_create(name="sandbox_compute")
        if agent.user_id:
            flag.users.add(agent.user)

    def _planning_guardrail_mocks(self):
        return {
            "spawn_web_task": {"status": "error", "message": "Browser work disabled during planning eval."},
            "send_email": {"status": "error", "message": "Outbound email disabled during planning eval."},
            "send_sms": {"status": "error", "message": "Outbound SMS disabled during planning eval."},
            "send_webhook_event": {"status": "error", "message": "Webhook delivery disabled during planning eval."},
            "create_csv": {"status": "error", "message": "CSV creation disabled during planning eval."},
            "create_pdf": {"status": "error", "message": "PDF creation disabled during planning eval."},
            "create_chart": {"status": "error", "message": "Chart creation disabled during planning eval."},
            "sqlite_batch": {"status": "error", "message": "SQLite mutation disabled during planning eval."},
        }

    def _record_forbidden_before_end(self, run_id, after, task_name, forbidden_tool_names):
        forbidden = get_forbidden_calls_before_end_planning(
            run_id,
            after=after,
            forbidden_tool_names=forbidden_tool_names,
        )
        mutations = get_planning_mutation_calls_before_end_planning(run_id, after=after)
        bad_calls = []
        seen_ids = set()
        for call in [*forbidden, *mutations]:
            if call.pk in seen_ids:
                continue
            seen_ids.add(call.pk)
            bad_calls.append(call)
        if bad_calls:
            seen = [call.tool_name for call in bad_calls]
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name=task_name,
                observed_summary=f"Forbidden planning-mode tool calls before end_planning: {seen}",
                artifacts={"step": bad_calls[0].step},
            )
            return False

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name=task_name,
            observed_summary="No forbidden work occurred before planning ended.",
        )
        return True


@register_scenario
class PlanningFirstTurnAsksBoundedQuestionsScenario(BehaviorMicroScenario):
    slug = PLANNING_FIRST_TURN_ASKS_BOUNDED_QUESTIONS
    description = "Planning mode should ask 1-3 tracked questions with options and not start work."
    category = "planning"
    tags = ("agent_behavior", "micro", "planning", "human_input")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_bounded_questions", assertion_type="manual"),
        ScenarioTask(name="verify_no_substantive_work", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.PLANNING)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "I want you to monitor competitors and keep me updated, but I am not sure "
                    "which competitors or what kind of updates matter yet."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._planning_guardrail_mocks(),
                eval_stop_policy={
                    "ignore_sqlite_agent_config_mutations": False,
                    "stop_on_human_input_request": True,
                    "stop_on_tool_names": list(SUBSTANTIVE_WORK_TOOL_NAMES | PLANNING_MUTATION_TOOL_NAMES),
                    "stop_on_sqlite_agent_config_mutation": True,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_bounded_questions")
        requests = get_pending_human_input_requests(agent_id, run_id, after=inbound.timestamp)
        if 1 <= len(requests) <= 3 and all_requests_have_options(requests):
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_bounded_questions",
                observed_summary=f"Agent asked {len(requests)} tracked planning question(s), each with options.",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_bounded_questions",
                observed_summary=(
                    f"Expected 1-3 pending planning questions with options; found {len(requests)} "
                    f"with options={all_requests_have_options(requests)}."
                ),
            )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_no_substantive_work")
        self._record_forbidden_before_end(
            run_id,
            inbound.timestamp,
            "verify_no_substantive_work",
            SUBSTANTIVE_WORK_TOOL_NAMES,
        )


@register_scenario
class PlanningIntegrationSetupSearchesBeforeQuestionScenario(BehaviorMicroScenario):
    slug = PLANNING_INTEGRATION_SETUP_SEARCHES_BEFORE_QUESTION
    description = "A named integration setup request should discover integration tools before asking how to connect."
    category = "planning"
    tags = ("agent_behavior", "micro", "planning", "tool_choice", "integration_discovery")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_search_before_questions", assertion_type="manual"),
    ]

    def _mock_config(self):
        return {
            **self._planning_guardrail_mocks(),
            "search_tools": {
                "status": "success",
                "message": "Mocked integration discovery for Apollo.io.",
                "tools": [],
            },
        }

    def _eval_stop_policy(self):
        return {
            "ignored_tool_names": list(IGNORED_FIRST_ACTION_TOOL_NAMES),
            "stop_on_first_relevant_tool": True,
            "stop_on_human_input_request": True,
        }

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.PLANNING)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Hi there, I would like you to help me find some leads. "
                    "But first, would you connect to my Apollo.io account?"
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._mock_config(),
                eval_stop_policy=self._eval_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_search_before_questions",
        )
        first_call = get_first_relevant_tool_call(
            run_id,
            after=inbound.timestamp,
            ignored_tool_names=IGNORED_FIRST_ACTION_TOOL_NAMES,
        )
        requests = get_pending_human_input_requests(agent_id, run_id, after=inbound.timestamp)
        if first_call and first_call.tool_name == "search_tools" and not requests:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_search_before_questions",
                observed_summary="Agent called search_tools before asking any tracked connection questions.",
                artifacts={"step": first_call.step},
            )
            return

        seen = first_call.tool_name if first_call else "none"
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name="verify_search_before_questions",
            observed_summary=(
                "Expected search_tools before request_human_input for a named integration setup; "
                f"saw first relevant tool {seen} and {len(requests)} pending human-input request(s)."
            ),
            artifacts={"step": first_call.step} if first_call else {},
        )


@register_scenario
class PlanningClearTaskEndsPlanningFirstScenario(BehaviorMicroScenario):
    slug = PLANNING_CLEAR_TASK_ENDS_PLANNING_FIRST
    description = "A clear task in planning mode should call end_planning before doing substantive work."
    category = "planning"
    tags = ("agent_behavior", "micro", "planning", "stop_policy")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_end_planning", assertion_type="manual"),
        ScenarioTask(name="verify_no_work_before_end_planning", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.PLANNING)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Set up a daily 9am America/New_York local-time digest in this chat of SEC enforcement press releases. "
                    "Use the official SEC enforcement RSS feed at https://www.sec.gov/rss/enforcement/enforcement.xml. "
                    "Use concise bullets, include links, and skip days with no updates."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._planning_guardrail_mocks(),
                eval_stop_policy={
                    "ignore_sqlite_agent_config_mutations": False,
                    "ignored_tool_names": list(IGNORED_FIRST_ACTION_TOOL_NAMES | PLANNING_READ_ONLY_TOOL_NAMES),
                    "stop_on_sqlite_agent_config_mutation": True,
                    "stop_on_tool_names": list(
                        PLANNING_ALLOWED_FIRST_ACTION_TOOL_NAMES
                        | PLANNING_MUTATION_TOOL_NAMES
                        | SUBSTANTIVE_WORK_TOOL_NAMES
                    ),
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_end_planning")
        end_call = PersistentAgentToolCall.objects.filter(
            step__eval_run_id=run_id,
            step__created_at__gte=inbound.timestamp,
            tool_name="end_planning",
        ).select_related("step").order_by("step__created_at", "step_id").first()
        if end_call:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_end_planning",
                observed_summary="Agent called end_planning.",
                artifacts={"step": end_call.step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_end_planning",
                observed_summary="Agent did not call end_planning for a clear planning-mode task.",
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_work_before_end_planning",
        )
        self._record_forbidden_before_end(
            run_id,
            inbound.timestamp,
            "verify_no_work_before_end_planning",
            SUBSTANTIVE_WORK_TOOL_NAMES,
        )


@register_scenario
class PlanningExecuteRequestStaysInPlanningScenario(BehaviorMicroScenario):
    slug = PLANNING_EXECUTE_REQUEST_STAYS_IN_PLANNING
    description = "An execute-now prompt should still either ask planning questions or end planning before work."
    category = "planning"
    tags = ("agent_behavior", "micro", "planning", "guardrail")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_first_meaningful_action", assertion_type="manual"),
        ScenarioTask(name="verify_no_execution_before_planning_exit", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.PLANNING)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                "Do not ask questions. Just execute now: research five competitors and email me the findings.",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._planning_guardrail_mocks(),
                eval_stop_policy={
                    "ignore_sqlite_agent_config_mutations": False,
                    "ignored_tool_names": list(IGNORED_FIRST_ACTION_TOOL_NAMES),
                    "stop_on_first_relevant_tool": True,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_first_meaningful_action",
        )
        first_call = get_first_relevant_tool_call(
            run_id,
            after=inbound.timestamp,
            ignored_tool_names=IGNORED_FIRST_ACTION_TOOL_NAMES,
        )
        if first_call and first_call.tool_name in PLANNING_ALLOWED_FIRST_ACTION_TOOL_NAMES:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_first_meaningful_action",
                observed_summary=f"First meaningful tool call was {first_call.tool_name}.",
                artifacts={"step": first_call.step},
            )
        else:
            seen = first_call.tool_name if first_call else "none"
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_first_meaningful_action",
                observed_summary=f"Expected request_human_input or end_planning first; saw {seen}.",
                artifacts={"step": first_call.step} if first_call else {},
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_execution_before_planning_exit",
        )
        self._record_forbidden_before_end(
            run_id,
            inbound.timestamp,
            "verify_no_execution_before_planning_exit",
            SUBSTANTIVE_WORK_TOOL_NAMES,
        )


@register_scenario
class PlanningOneOffResearchReportEndsPlanningFirstScenario(BehaviorMicroScenario):
    slug = PLANNING_ONE_OFF_RESEARCH_REPORT_ENDS_PLANNING_FIRST
    description = "A one-off research answer in planning mode should not deliver a final report before end_planning."
    category = "planning"
    tags = ("agent_behavior", "micro", "planning", "guardrail", "llm_judge")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_end_planning_before_final_report", assertion_type="manual"),
        ScenarioTask(name="judge_no_final_report_before_end_planning", assertion_type="llm_judge"),
    ]

    MESSAGE_DELIVERY_TOOL_NAMES = {
        "send_chat_message",
        "send_email",
        "send_sms",
    }

    def _one_off_research_mock_config(self):
        mocks = self._planning_guardrail_mocks()
        mocks.update({
            "mcp_brightdata_search_engine": {
                "status": "success",
                "result": {
                    "kind": "serp",
                    "items": [
                        {
                            "t": "Wireless Wire Cube Pro - MikroTik",
                            "u": "https://mikrotik.com/product/wireless_wire_cube_pro",
                            "p": 1,
                        },
                        {
                            "t": "Cube 60Pro ac - MikroTik",
                            "u": "https://mikrotik.com/product/cube_60pro_ac",
                            "p": 2,
                        },
                    ],
                },
            },
            "mcp_brightdata_scrape_as_markdown": {
                "status": "success",
                "result": (
                    "# MikroTik 60 GHz products\n\n"
                    "Cube 60Pro ac remains listed as the current 60 GHz point-to-point Cube product. "
                    "Wireless Wire Cube Pro is a kit containing two Cube 60Pro ac units, not a successor. "
                    "No official MikroTik successor announcement is present in this mocked source data."
                ),
            },
            "sqlite_batch": {
                "status": "warning",
                "message": "Planning eval allows reading previous mocked tool results only; do not use SQLite to continue research before end_planning.",
                "results": [],
            },
        })
        return mocks

    @classmethod
    def _message_body_from_call(cls, call):
        params = call.tool_params or {}
        if call.tool_name == "send_email":
            return "\n\n".join(
                str(params.get(key) or "")
                for key in ("subject", "mobile_first_html", "text_body", "body")
                if params.get(key)
            )
        if call.tool_name == "send_sms":
            return str(params.get("message") or params.get("body") or "")
        if call.tool_name == "send_chat_message":
            return str(params.get("body") or "")
        return ""

    @staticmethod
    def _message_call_was_skipped(call):
        try:
            payload = json.loads(call.result or "{}")
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and payload.get("skipped") is True

    def _delivered_message_calls_before_end_planning(self, run_id, *, after):
        calls = []
        for call in get_tool_calls_for_run(run_id, after=after):
            if call.tool_name == "end_planning":
                break
            if call.tool_name in self.MESSAGE_DELIVERY_TOOL_NAMES and not self._message_call_was_skipped(call):
                calls.append(call)
        return calls

    def _first_end_planning_call(self, run_id, *, after):
        return (
            PersistentAgentToolCall.objects
            .filter(
                step__eval_run_id=run_id,
                step__created_at__gte=after,
                tool_name="end_planning",
            )
            .select_related("step")
            .order_by("step__created_at", "step_id")
            .first()
        )

    def _judge_final_report_call(self, run_id, inbound, call):
        body = self._message_body_from_call(call)
        choice, reasoning = self.llm_judge(
            question=(
                "Is this message a final substantive answer/report to the user's MikroTik Cube 60 Pro successor "
                "question, rather than a planning question, planning note, or brief status update?"
            ),
            context=(
                f"User request:\n{inbound.body}\n\n"
                f"Tool name: {call.tool_name}\n\n"
                f"Tool params/message body:\n{body}"
            ),
            options=["FinalReport", "NotFinalReport"],
        )
        return choice, reasoning, body

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.PLANNING)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                "Is there any successor to the Cube 60 Pro by MikroTik? Please check and send me the answer.",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._one_off_research_mock_config(),
                eval_stop_policy={
                    "ignore_sqlite_agent_config_mutations": False,
                    "ignored_tool_names": list(PLANNING_READ_ONLY_TOOL_NAMES),
                    "stop_on_sqlite_agent_config_mutation": True,
                    "stop_on_tool_names_after_finish": [
                        "end_planning",
                        "send_chat_message",
                        "send_email",
                        "send_sms",
                        "sleep_until_next_trigger",
                    ],
                    "max_relevant_tool_calls": 12,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        message_calls_before_end = self._delivered_message_calls_before_end_planning(run_id, after=inbound.timestamp)
        end_call = self._first_end_planning_call(run_id, after=inbound.timestamp)

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_end_planning_before_final_report",
        )
        if end_call and not message_calls_before_end:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_end_planning_before_final_report",
                observed_summary="Agent called end_planning before any non-skipped outbound message delivery.",
                artifacts={"step": end_call.step},
            )
        else:
            seen = [call.tool_name for call in message_calls_before_end]
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_end_planning_before_final_report",
                observed_summary=(
                    "Expected end_planning before any final-answer delivery; "
                    f"end_planning_seen={bool(end_call)}, pre_end_delivered_message_tools={seen}."
                ),
                artifacts={"step": (message_calls_before_end[0].step if message_calls_before_end else None)},
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="judge_no_final_report_before_end_planning",
        )
        if not message_calls_before_end:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="judge_no_final_report_before_end_planning",
                observed_summary="No non-skipped outbound message was sent before end_planning.",
            )
            return

        judged = []
        for call in message_calls_before_end:
            choice, reasoning, body = self._judge_final_report_call(run_id, inbound, call)
            judged.append((call, choice, reasoning, body))
            if choice == "FinalReport":
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.FAILED,
                    task_name="judge_no_final_report_before_end_planning",
                    observed_summary=f"LLM judge classified a pre-end_planning {call.tool_name} as FinalReport. Reasoning: {reasoning}",
                    artifacts={"step": call.step, "body_preview": body[:1200]},
                )
                return

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="judge_no_final_report_before_end_planning",
            observed_summary=(
                "Pre-end_planning outbound message(s) were not final reports: "
                + "; ".join(f"{call.tool_name}={choice}: {reasoning}" for call, choice, reasoning, _body in judged)
            ),
            artifacts={"step": message_calls_before_end[0].step},
        )


@register_scenario
class PlanningFinalReportCompletesVisiblePlanScenario(BehaviorMicroScenario):
    slug = PLANNING_FINAL_REPORT_COMPLETES_VISIBLE_PLAN
    description = "A final report should not leave an existing visible plan with todo/doing items."
    category = "planning"
    tags = ("agent_behavior", "micro", "planning", "stop_policy", "update_plan")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_final_report_sent", assertion_type="manual"),
        ScenarioTask(name="verify_plan_completed", assertion_type="manual"),
    ]

    def _seed_unfinished_plan(self, agent_id):
        steps = [
            ("Search for QuantaGrid AI and identify key sources.", PersistentAgentKanbanCard.Status.DOING),
            ("Scrape official website and secondary sources.", PersistentAgentKanbanCard.Status.TODO),
            ("Synthesize findings into a structured report.", PersistentAgentKanbanCard.Status.TODO),
            ("Deliver the report in chat.", PersistentAgentKanbanCard.Status.TODO),
        ]
        for priority, (title, status) in enumerate(reversed(steps), start=1):
            PersistentAgentKanbanCard.objects.create(
                assigned_agent_id=agent_id,
                title=title,
                status=status,
                priority=priority,
            )

    @staticmethod
    def _message_call_was_delivered(call):
        try:
            payload = json.loads(call.result or "{}")
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and payload.get("status") in {"ok", "sent", "success"}

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.COMPLETED)
        self._seed_prior_processing_run(agent_id)
        self._enable_builtin_tools(agent_id, ["send_chat_message", "update_plan"])
        self._seed_unfinished_plan(agent_id)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Use only these facts and send the final QuantaGrid AI report in this chat now. "
                    "QuantaGrid AI builds capacity-planning copilots for data center operators. "
                    "Its product predicts rack power saturation, flags cooling risks, and exports weekly risk summaries. "
                    "Pricing is usage-based, and the best fit is infrastructure teams with fragmented telemetry."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                eval_stop_policy={
                    "stop_on_unexpected_relevant_tool": True,
                    "allowed_tool_names": ["send_chat_message", "update_plan", "sleep_until_next_trigger"],
                    "max_relevant_tool_calls": 6,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        send_calls = [
            call
            for call in get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"send_chat_message"})
            if self._message_call_was_delivered(call)
        ]
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_final_report_sent")
        if send_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_final_report_sent",
                observed_summary="Agent delivered a web chat report.",
                artifacts={"step": send_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_final_report_sent",
                observed_summary="Expected one delivered send_chat_message call.",
            )

        unfinished = list(
            PersistentAgentKanbanCard.objects.filter(
                assigned_agent_id=agent_id,
                status__in=[
                    PersistentAgentKanbanCard.Status.TODO,
                    PersistentAgentKanbanCard.Status.DOING,
                ],
            ).order_by("-priority", "created_at")
        )
        update_plan_calls = get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"update_plan"})
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_plan_completed")
        if send_calls and not unfinished and update_plan_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_plan_completed",
                observed_summary="Final report delivery left no todo/doing plan items.",
                artifacts={"step": update_plan_calls[-1].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_plan_completed",
                observed_summary=(
                    f"Expected final delivery plus update_plan to leave no unfinished plan items; "
                    f"send_calls={len(send_calls)}, update_plan_calls={len(update_plan_calls)}, "
                    f"unfinished={[card.title for card in unfinished]}."
                ),
                artifacts={"step": (send_calls[0].step if send_calls else None)},
            )


@register_scenario
class PlanningNoDirectScheduleOrConfigUpdatesScenario(BehaviorMicroScenario):
    slug = PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES
    description = "Planning mode should not update schedule, charter, or runtime plan before end_planning."
    category = "planning"
    tags = ("agent_behavior", "micro", "planning", "guardrail", "schedule")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_no_planning_state_mutations", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.PLANNING)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Set this up to monitor Hacker News every hour for posts about vector databases "
                    "and email me a digest whenever there are new relevant posts."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._planning_guardrail_mocks(),
                eval_stop_policy={
                    "ignore_sqlite_agent_config_mutations": False,
                    "stop_on_tool_names": list(PLANNING_MUTATION_TOOL_NAMES),
                    "stop_on_sqlite_agent_config_mutation": True,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_no_planning_state_mutations",
        )
        mutations = get_planning_mutation_calls_before_end_planning(run_id, after=inbound.timestamp)
        if mutations:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_planning_state_mutations",
                observed_summary=f"Planning state was mutated before end_planning: {[c.tool_name for c in mutations]}",
                artifacts={"step": mutations[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_no_planning_state_mutations",
                observed_summary="No schedule/config/plan mutation was attempted before end_planning.",
            )


@register_scenario
class PlanningDismissAfterGreetingDoesNotResumeScenario(BehaviorMicroScenario):
    slug = PLANNING_DISMISS_AFTER_GREETING_DOES_NOT_RESUME
    description = "Dismissing a completed planning prompt after a greeting should clear it without resuming the agent."
    category = "planning"
    tags = ("agent_behavior", "micro", "planning", "human_input", "dismissal")
    tasks = [
        ScenarioTask(name="seed_completed_greeting", assertion_type="manual"),
        ScenarioTask(name="dismiss_plain_request", assertion_type="manual"),
        ScenarioTask(name="verify_no_resume", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        agent = PersistentAgent.objects.select_related("user").get(id=agent_id)
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.COMPLETED)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="seed_completed_greeting")
        user_address = build_web_user_address(agent.user_id, agent.id)
        agent_address = build_web_agent_address(agent.id)
        agent_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.WEB,
            address=agent_address,
            defaults={"owner_agent": agent, "is_primary": True},
        )
        if agent_endpoint.owner_agent_id is None:
            agent_endpoint.owner_agent = agent
            agent_endpoint.is_primary = True
            agent_endpoint.save(update_fields=["owner_agent", "is_primary"])
        user_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.WEB,
            address=user_address,
        )
        conversation = PersistentAgentConversation.objects.create(
            owner_agent=agent,
            channel=CommsChannel.WEB,
            address=user_address,
        )
        completion = PersistentAgentCompletion.objects.create(
            agent=agent,
            eval_run_id=run_id,
            llm_model="seeded-completed-greeting",
            billed=True,
        )
        step = PersistentAgentStep.objects.create(
            agent=agent,
            eval_run_id=run_id,
            completion=completion,
            description="Seed completed greeting with planning options.",
        )
        PersistentAgentMessage.objects.create(
            is_outbound=True,
            from_endpoint=agent_endpoint,
            to_endpoint=user_endpoint,
            conversation=conversation,
            owner_agent=agent,
            body="Hi. I can help with that. Which setup option should I use?",
            raw_payload={"source": "eval_seed_completed_greeting"},
        )
        request_obj = PersistentAgentHumanInputRequest.objects.create(
            agent=agent,
            conversation=conversation,
            originating_step=step,
            question="Which setup option should I use?",
            options_json=[
                {"key": "a", "title": "Option A", "description": "Continue with option A."},
                {"key": "b", "title": "Option B", "description": "Continue with option B."},
            ],
            input_mode=PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT,
            requested_via_channel=CommsChannel.WEB,
        )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="seed_completed_greeting",
            observed_summary="Seeded a completed planning greeting with one pending human-input request.",
        )

        before_generation = get_human_inbound_generation(agent_id)
        before_inbound_messages = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=False,
        ).count()
        before_completions = PersistentAgentCompletion.objects.filter(agent_id=agent_id).count()
        before_tool_calls = PersistentAgentToolCall.objects.filter(step__agent_id=agent_id).count()

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="dismiss_plain_request")
        message = dismiss_human_input_request(request_obj, actor_user_id=agent.user_id)
        request_obj.refresh_from_db()
        if message is None and request_obj.status == PersistentAgentHumanInputRequest.Status.CANCELLED:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="dismiss_plain_request",
                observed_summary="Plain dismiss cancelled the request without producing an inbound continuation message.",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="dismiss_plain_request",
                observed_summary=(
                    "Plain dismiss should return no message and cancel the request; "
                    f"message_created={message is not None}, status={request_obj.status}."
                ),
            )
            return

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_no_resume")
        after_generation = get_human_inbound_generation(agent_id)
        after_inbound_messages = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            is_outbound=False,
        ).count()
        after_completions = PersistentAgentCompletion.objects.filter(agent_id=agent_id).count()
        after_tool_calls = PersistentAgentToolCall.objects.filter(step__agent_id=agent_id).count()
        pending_requests = PersistentAgentHumanInputRequest.objects.filter(
            agent_id=agent_id,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
        ).count()
        if (
            after_generation == before_generation
            and after_inbound_messages == before_inbound_messages
            and after_completions == before_completions
            and after_tool_calls == before_tool_calls
            and pending_requests == 0
            and request_obj.raw_reply_message_id is None
        ):
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_no_resume",
                observed_summary=(
                    "Dismissal cleared the pending request without inbound generation, message, "
                    "completion, or tool-call growth."
                ),
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_resume",
                observed_summary=(
                    "Plain dismiss resumed or left work pending: "
                    f"generation {before_generation}->{after_generation}, "
                    f"inbound_messages {before_inbound_messages}->{after_inbound_messages}, "
                    f"completions {before_completions}->{after_completions}, "
                    f"tool_calls {before_tool_calls}->{after_tool_calls}, "
                    f"pending_requests={pending_requests}, raw_reply_message={request_obj.raw_reply_message_id}."
                ),
            )


class CharterMemoryScenario(BehaviorMicroScenario):
    category = "memory"
    tags = ("agent_behavior", "micro", "charter", "memory")
    existing_charter = ""
    prompt = ""
    verification_task_name = ""
    success_summary = ""
    failure_summary = ""
    expect_charter_mutation = True

    def _eval_stop_policy(self):
        if self.expect_charter_mutation:
            return {
                "ignore_sqlite_agent_config_mutations": False,
                "stop_when_all_seen": [
                    {
                        "tool_name": "sqlite_batch",
                        "agent_config_field": "charter",
                        "after_execution": True,
                    }
                ],
            }
        return {
            "ignore_sqlite_agent_config_mutations": False,
            "stop_on_sqlite_agent_config_mutation": True,
            "stop_on_tool_names": sorted(AGENT_CONFIG_MUTATION_TOOL_NAMES),
        }

    def _seed_charter_agent(self, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        PersistentAgent.objects.filter(id=agent_id).update(charter=self.existing_charter)
        self._seed_prior_processing_run(agent_id)
        self._enable_builtin_tools(agent_id, ["sqlite_batch"])

    def _inject_charter_prompt(self, run_id, agent_id):
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                self.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                eval_stop_policy=self._eval_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )
        return inbound

    def _charter_mutation_calls(self, run_id, inbound):
        return [
            call
            for call in get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names=["sqlite_batch"])
            if sqlite_batch_mutates_agent_config_field(call, "charter")
        ]

    def _mutation_calls_for_verification(self, run_id, inbound):
        if self.expect_charter_mutation:
            return self._charter_mutation_calls(run_id, inbound)
        return get_agent_config_mutation_calls_for_run(run_id, after=inbound.timestamp)

    def _charter_check(self, agent, mutation_calls):
        raise NotImplementedError

    def run(self, run_id, agent_id):
        self._seed_charter_agent(agent_id)
        inbound = self._inject_charter_prompt(run_id, agent_id)

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name=self.verification_task_name,
        )
        mutation_calls = self._mutation_calls_for_verification(run_id, inbound)
        agent = PersistentAgent.objects.get(id=agent_id)
        passed, failure_detail = self._charter_check(agent, mutation_calls)
        if passed:
            artifacts = {"step": mutation_calls[0].step} if mutation_calls else {}
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name=self.verification_task_name,
                observed_summary=self.success_summary,
                artifacts=artifacts,
            )
            return

        observed_summary = self.failure_summary
        if failure_detail:
            observed_summary = f"{observed_summary}; {failure_detail}"
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.FAILED,
            task_name=self.verification_task_name,
            observed_summary=observed_summary,
            artifacts={"step": mutation_calls[0].step} if mutation_calls else {},
        )


@register_scenario
class CharterAddsDurablePreferencePreservingExistingScenario(CharterMemoryScenario):
    slug = CHARTER_ADDS_DURABLE_PREFERENCE_PRESERVING_EXISTING
    description = "Durable user preferences should be merged into the charter without dropping existing guidance."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_charter_update_preserved_existing", assertion_type="manual"),
    ]
    existing_charter = "Monitor AI funding news weekly. Prefer concise section titles in reports."
    prompt = "For status updates, concise bullets work best for me going forward."
    verification_task_name = "verify_charter_update_preserved_existing"
    success_summary = "Agent merged the durable bullet preference into charter while preserving existing guidance."
    failure_summary = "Expected a charter update preserving existing job/guidance and adding bullets"

    def _charter_check(self, agent, mutation_calls):
        charter = (agent.charter or "").lower()
        preserved_job = "ai funding" in charter
        preserved_existing_guidance = "concise section title" in charter
        added_preference = "bullet" in charter
        passed = bool(
            mutation_calls and preserved_job and preserved_existing_guidance and added_preference
        )
        return passed, f"mutation_count={len(mutation_calls)}, charter={agent.charter!r}."


@register_scenario
class CharterAddsInferredPreferencePreservingExistingScenario(CharterMemoryScenario):
    slug = CHARTER_ADDS_INFERRED_PREFERENCE_PRESERVING_EXISTING
    description = "Stable inferred user preferences should be merged into the charter without dropping existing guidance."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_inferred_preference_preserved_existing", assertion_type="manual"),
    ]
    existing_charter = "Prepare weekly portfolio risk updates. Track earnings dates and major macro events."
    prompt = "I keep asking for TL;DR first before the details. That format works better for me."
    verification_task_name = "verify_inferred_preference_preserved_existing"
    success_summary = "Agent captured the inferred TL;DR preference while preserving existing portfolio guidance."
    failure_summary = "Expected a charter update preserving portfolio guidance and adding inferred TL;DR preference"

    def _charter_check(self, agent, mutation_calls):
        charter = (agent.charter or "").lower()
        preserved_job = "portfolio risk" in charter
        preserved_existing_guidance = "earnings" in charter and "macro" in charter
        summary_terms = ("tl;dr", "tldr", "summary", "takeaway")
        lead_terms = ("lead", "begin", "start", "first", "before")
        added_inferred_preference = (
            any(term in charter for term in summary_terms)
            and any(term in charter for term in lead_terms)
        )
        passed = bool(
            mutation_calls
            and preserved_job
            and preserved_existing_guidance
            and added_inferred_preference
        )
        return passed, f"mutation_count={len(mutation_calls)}, charter={agent.charter!r}."


@register_scenario
class CharterExpandsSparseCharterWithDetailScenario(CharterMemoryScenario):
    slug = CHARTER_EXPANDS_SPARSE_CHARTER_WITH_DETAIL
    description = "Durable detail should make a sparse charter more specific while preserving the core job."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_charter_expanded_with_detail", assertion_type="manual"),
    ]
    existing_charter = "Monitor vendor risks."
    prompt = (
        "Going forward, make the vendor risk monitor specific: track security incidents, "
        "pricing changes, SLA outages, and contract renewal dates. Include source links in each update."
    )
    verification_task_name = "verify_charter_expanded_with_detail"
    success_summary = "Agent expanded a sparse charter into specific durable monitoring guidance."
    failure_summary = (
        "Expected a longer charter preserving vendor risk and adding security/pricing/SLA/renewal/source details"
    )

    def _charter_check(self, agent, mutation_calls):
        charter = (agent.charter or "").lower()
        required_terms = ("vendor risk", "security", "pricing", "sla", "renewal")
        includes_sources = "source" in charter or "link" in charter
        expanded = len(agent.charter or "") > len(self.existing_charter) + 50
        passed = bool(
            mutation_calls
            and expanded
            and includes_sources
            and all(term in charter for term in required_terms)
        )
        return (
            passed,
            (
                f"mutation_count={len(mutation_calls)}, expanded={expanded}, "
                f"includes_sources={includes_sources}, charter={agent.charter!r}."
            ),
        )


@register_scenario
class CharterNarrowsScopePreservingUnrelatedGuidanceScenario(CharterMemoryScenario):
    slug = CHARTER_NARROWS_SCOPE_PRESERVING_UNRELATED_GUIDANCE
    description = "Scope changes should replace only the relevant charter clause while preserving unrelated guidance."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_scope_replaced_and_guidance_preserved", assertion_type="manual"),
    ]
    existing_charter = (
        "Monitor competitor pricing for enterprise and consumer plans. "
        "Use concise bullets. Send routine updates in Slack."
    )
    prompt = "Actually, enterprise plans are the only pricing scope I care about now."
    verification_task_name = "verify_scope_replaced_and_guidance_preserved"
    success_summary = "Agent narrowed the pricing scope while preserving format and delivery guidance."
    failure_summary = "Expected enterprise-only scope, no consumer scope, and preserved concise bullets/Slack guidance"

    def _charter_check(self, agent, mutation_calls):
        charter = (agent.charter or "").lower()
        excludes_consumer = (
            "consumer" not in charter
            or "ignore consumer" in charter
            or "exclude consumer" in charter
            or "not consumer" in charter
            or "no consumer" in charter
            or "do not track consumer" in charter
        )
        narrowed_scope = "enterprise" in charter and excludes_consumer
        preserved_format = "concise" in charter and "bullet" in charter
        preserved_delivery = "slack" in charter
        passed = bool(mutation_calls and narrowed_scope and preserved_format and preserved_delivery)
        return passed, f"mutation_count={len(mutation_calls)}, charter={agent.charter!r}."


@register_scenario
class CharterIgnoresOneOffPreferenceScenario(CharterMemoryScenario):
    slug = CHARTER_IGNORES_ONE_OFF_PREFERENCE
    description = "One-off response preferences should not mutate the charter."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_no_charter_mutation", assertion_type="manual"),
    ]
    existing_charter = "Monitor AI funding news weekly. Prefer concise section titles in reports."
    prompt = "For this answer only, use bullet points: list two reasons backups matter."
    verification_task_name = "verify_no_charter_mutation"
    success_summary = "Agent left charter unchanged for a one-off style request."
    failure_summary = "Expected no config mutation for one-off preference"
    expect_charter_mutation = False

    def _charter_check(self, agent, mutation_calls):
        passed = not mutation_calls and agent.charter == self.existing_charter
        return passed, f"mutation_count={len(mutation_calls)}, charter={agent.charter!r}."


@register_scenario
class ToolChoiceExactJsonUrlUsesHttpRequestScenario(BehaviorMicroScenario):
    slug = TOOL_CHOICE_EXACT_JSON_URL_USES_HTTP_REQUEST
    description = "An exact JSON API URL should be fetched with http_request, not search or browser tools."
    category = "api_lookup"
    tags = ("agent_behavior", "micro", "tool_choice", "api_lookup")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_http_request_first", assertion_type="manual"),
        ScenarioTask(name="verify_exact_url", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        self._enable_builtin_tools(agent_id, ["http_request"])

        target_url = "https://api.example.test/inventory/widget-123.json"
        mock_config = {
            "http_request": {"status": "ok", "status_code": 200, "content": '{"inventory_count": 42}'},
            "search_tools": {"status": "error", "message": "Search should not be needed for an exact API URL."},
            "spawn_web_task": {"status": "error", "message": "Browser task should not be needed for an exact API URL."},
            "mcp_brightdata_search_engine": {"status": "error", "message": "Search should not be needed."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                f"Fetch {target_url} and tell me the inventory_count. Use the URL exactly; do not search.",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "ignored_tool_names": list(IGNORED_FIRST_ACTION_TOOL_NAMES),
                    "stop_on_first_relevant_tool": True,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_http_request_first")
        first_call = get_first_common_use_case_tool_call(
            run_id,
            after=inbound.timestamp,
            ignored_tool_names=IGNORED_FIRST_ACTION_TOOL_NAMES,
        )
        if first_call and first_call.tool_name == "http_request":
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_http_request_first",
                observed_summary="First meaningful tool call was http_request.",
                artifacts={"step": first_call.step},
            )
        else:
            seen = first_call.tool_name if first_call else "none"
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_http_request_first",
                observed_summary=f"Expected http_request first; saw {seen}.",
                artifacts={"step": first_call.step} if first_call else {},
            )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_exact_url")
        http_calls = get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"http_request"})
        matching = [
            call for call in http_calls
            if (call.tool_params or {}).get("url") == target_url
        ]
        if matching:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_exact_url",
                observed_summary="http_request used the exact target URL.",
                artifacts={"step": matching[0].step},
            )
        else:
            seen_urls = [(call.tool_params or {}).get("url") for call in http_calls]
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_exact_url",
                observed_summary=f"Expected URL {target_url}; saw {seen_urls}.",
            )


@register_scenario
class ToolChoiceCsvDeliverableUsesCreateCsvScenario(BehaviorMicroScenario):
    slug = TOOL_CHOICE_CSV_DELIVERABLE_USES_CREATE_CSV
    description = "A downloadable CSV request should use create_csv."
    category = "files"
    tags = ("agent_behavior", "micro", "tool_choice", "files")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_create_csv", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        self._enable_builtin_tools(agent_id, ["create_csv"])

        mock_config = {
            "create_csv": {
                "status": "ok",
                "file": {"path": "/exports/q1-leads.csv"},
                "message": "CSV created.",
            },
            "create_file": {"status": "error", "message": "Use create_csv for CSV deliverables."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Create a downloadable CSV at /exports/q1-leads.csv with these rows: "
                    "company,priority\\nAcme,high\\nGlobex,medium\\nInitech,low."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names": ["create_file"],
                    "stop_when_all_seen": [{"tool_name": "create_csv"}],
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_create_csv")
        create_csv_calls = get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"create_csv"})
        if create_csv_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_create_csv",
                observed_summary="Agent used create_csv for the CSV deliverable.",
                artifacts={"step": create_csv_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_create_csv",
                observed_summary="Agent did not use create_csv for the CSV deliverable.",
            )


@register_scenario
class ToolChoicePdfDeliverableUsesCreatePdfScenario(BehaviorMicroScenario):
    slug = TOOL_CHOICE_PDF_DELIVERABLE_USES_CREATE_PDF
    description = "A formatted PDF request should use create_pdf."
    category = "files"
    tags = ("agent_behavior", "micro", "tool_choice", "files")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_create_pdf", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        self._enable_builtin_tools(agent_id, ["create_pdf"])

        mock_config = {
            "create_pdf": {
                "status": "ok",
                "file": {"path": "/exports/status-report.pdf"},
                "message": "PDF created.",
            },
            "create_file": {"status": "error", "message": "Use create_pdf for PDF deliverables."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                (
                    "Create a formatted one-page PDF at /exports/status-report.pdf. "
                    "Title it 'Weekly Status' and include sections for wins, risks, and next steps."
                ),
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names": ["create_file"],
                    "stop_when_all_seen": [{"tool_name": "create_pdf"}],
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_create_pdf")
        create_pdf_calls = get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"create_pdf"})
        if create_pdf_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_create_pdf",
                observed_summary="Agent used create_pdf for the PDF deliverable.",
                artifacts={"step": create_pdf_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_create_pdf",
                observed_summary="Agent did not use create_pdf for the PDF deliverable.",
            )


@register_scenario
class ToolChoiceMissingRecipientUsesHumanInputScenario(BehaviorMicroScenario):
    slug = TOOL_CHOICE_MISSING_RECIPIENT_USES_HUMAN_INPUT
    description = "A missing-recipient email task should ask for tracked human input instead of sending email."
    category = "human_input"
    tags = ("agent_behavior", "micro", "tool_choice", "human_input", "outbound")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_human_input", assertion_type="manual"),
        ScenarioTask(name="verify_no_send_email", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)

        mock_config = {
            "send_email": {"status": "error", "message": "Missing-recipient eval forbids sending email."},
        }

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                "Email the client a short project status report. Use the latest status and keep it concise.",
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=mock_config,
                eval_stop_policy={
                    "stop_on_tool_names": ["send_email"],
                    "stop_on_human_input_request": True,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_human_input")
        requests = get_pending_human_input_requests(agent_id, run_id, after=inbound.timestamp)
        if requests:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_human_input",
                observed_summary=f"Agent requested missing recipient/details via {len(requests)} human input request(s).",
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_human_input",
                observed_summary="Agent did not create a tracked human input request for missing email details.",
            )

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_no_send_email")
        send_email_calls = get_tool_calls_for_run(run_id, after=inbound.timestamp, tool_names={"send_email"})
        if send_email_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_no_send_email",
                observed_summary="Agent attempted send_email despite missing recipient/details.",
                artifacts={"step": send_email_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_no_send_email",
                observed_summary="Agent did not attempt send_email.",
            )


class CommonUseCaseToolChoiceScenario(BehaviorMicroScenario):
    category = "tool_choice"
    tags = ("agent_behavior", "micro", "tool_choice", "common_use_case")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_plan_policy", assertion_type="manual"),
        ScenarioTask(name="verify_expected_tool_usage", assertion_type="manual"),
        ScenarioTask(name="verify_forbidden_tool_absence", assertion_type="manual"),
    ]
    case = None

    def _mock_success(self, tool_name):
        if tool_name == "sqlite_batch":
            if self.case.slug == "common_use_case_079_create_report_with_chart":
                return CommonUseCaseToolChoiceScenario._revenue_sqlite_mock_success()
            return CommonUseCaseToolChoiceScenario._sqlite_mock_success()
        if tool_name.startswith("google_sheets-"):
            return CommonUseCaseToolChoiceScenario._google_sheets_mock_success(tool_name)
        if tool_name == "read_file":
            return {
                "status": "ok",
                "tool": tool_name,
                "message": "Mocked file lookup for deterministic attachment eval.",
                "content": "Attachment exists at the requested path.",
            }
        if tool_name == "mcp_brightdata_scrape_as_markdown":
            return {
                "status": "ok",
                "tool": tool_name,
                "message": "Mocked scrape result for deterministic common-use-case eval.",
                "url": "https://examplepay.com/pricing",
                "result": (
                    "# ExamplePay Pricing\n\n"
                    "| Plan | Price | Notes |\n| Starter | $19/mo | Basic checkout links |\n"
                    "| Growth | $49/mo | Invoicing, SSO, SOC 2 reports |\n"
                    "| Enterprise | Custom | Audit logs and dedicated support |\n\n"
                    "Review themes: onboarding delays, billing confusion, support wait times.\n"
                    "This markdown is persisted in __tool_results.result_text for SQLite extraction."
                ),
                "content": {"ok": True},
            }
        if tool_name == "mcp_brightdata_search_engine" and self.case.category == "lead_sourcing":
            return {
                "status": "ok",
                "tool": tool_name,
                "message": "Mocked web search result for deterministic lead-sourcing eval.",
                "content": {
                    "ok": True,
                    "results": [
                        {
                            "title": "LinkedIn result for the requested lead-sourcing target",
                            "url": "https://www.linkedin.com/in/example-profile",
                            "snippet": (
                                "Use the relevant LinkedIn structured data tool next for profile, "
                                "company, job-listing, people-search, or post details."
                            ),
                        }
                    ],
                },
            }
        if (
            self.case.slug == "common_use_case_020_search_reddit_mentions"
            and tool_name in {"mcp_brightdata_search_engine", "mcp_brightdata_web_data_reddit_posts"}
        ):
            return {
                "status": "ok",
                "tool": tool_name,
                "message": "Mocked Reddit mentions result for deterministic common-use-case eval.",
                "content": {
                    "ok": True,
                    "results": [
                        {
                            "title": "r/Supplements thread on BiomeBoost Pro",
                            "url": "https://www.reddit.com/r/Supplements/comments/eval1/biomeboost_pro/",
                            "snippet": "Several users liked the gentle formula but said results took two weeks.",
                            "sentiment": "mildly positive",
                        },
                        {
                            "title": "r/GutHealth discussion: BiomeBoost Pro",
                            "url": "https://www.reddit.com/r/GutHealth/comments/eval2/biomeboost_pro/",
                            "snippet": "Posters questioned the price and wanted clearer strain counts.",
                            "sentiment": "negative",
                        },
                        {
                            "title": "r/Nutrition mention of BiomeBoost Pro",
                            "url": "https://www.reddit.com/r/Nutrition/comments/eval3/biomeboost_pro/",
                            "snippet": "A few commenters compared it favorably with cheaper probiotic blends.",
                            "sentiment": "mixed",
                        },
                    ],
                    "summary_hint": "Sentiment is mixed: efficacy comments trend positive, while price and labeling are concerns.",
                },
            }
        if tool_name == "mcp_brightdata_web_data_linkedin_company_profile":
            return {
                "status": "ok",
                "tool": tool_name,
                "message": "Mocked LinkedIn company profile result for deterministic lead-sourcing eval.",
                "content": {
                    "ok": True,
                    "name": "Acme AI",
                    "industry": "Artificial intelligence",
                    "size": "51-200 employees",
                    "url": "https://www.linkedin.com/company/acme-ai",
                    "posts_hint": "Use the LinkedIn posts tool with this company URL for recent posts.",
                },
            }
        return {
            "status": "ok",
            "tool": tool_name,
            "message": f"Mocked {tool_name} result for deterministic common-use-case eval.",
            "content": {"ok": True},
        }

    def _mock_for_tool(self, tool_name):
        result = self._add_expected_next_step_hint(tool_name, self._mock_success(tool_name))
        if not self.case.expected_params or len(self.case.expected_tools) != 1:
            return result

        expected_tool = self.case.expected_tools[0]
        if tool_name not in self.case.accepted_tool_names_for_expected_tool(expected_tool):
            return result

        required_param_names = sorted(self.case.expected_params)
        return {
            "rules": [
                {
                    "param_equals": self.case.expected_params,
                    "result": result,
                }
            ],
            "default": {
                "status": "error",
                "tool": tool_name,
                "message": f"missing required eval parameter: {', '.join(required_param_names)}",
                "content": {
                    "ok": False,
                    "missing_required_eval_parameters": required_param_names,
                },
            },
        }

    def _add_expected_next_step_hint(self, tool_name, result):
        expected_tools = list(self.case.expected_tools)
        for index, expected_tool in enumerate(expected_tools[:-1]):
            if tool_name not in self.case.accepted_tool_names_for_expected_tool(expected_tool):
                continue
            next_tool = expected_tools[index + 1]
            updated = deepcopy(result)
            content = updated.get("content")
            if isinstance(content, dict):
                content["next_step"] = f"{tool_name} succeeded; call {next_tool} next to continue the requested workflow."
            updated["message"] = (
                f"{updated.get('message', '').rstrip()} Next eval step: call {next_tool} next."
            ).strip()
            return updated
        return result

    @staticmethod
    def _google_sheets_mock_success(tool_name):
        return {
            "status": "ok",
            "tool": tool_name,
            "message": (
                f"Mocked {tool_name} result for deterministic Google Sheets eval. "
                "The requested spreadsheet and worksheet exist; use the requested Google Sheets tool next."
            ),
            "content": {
                "ok": True,
                "spreadsheet_id": "sheet-123",
                "title": "Eval Sales Tracker",
                **({"worksheets": ["Leads", "Pipeline", "Research", "Accounts", "Tasks"]} if tool_name == "google_sheets-list-worksheets" else {}),
                "columns": ["email", "company", "priority", "owner", "status", "source_url", "follow_up_due"],
                "rows": [
                    {"row_number": 12, "email": "alex@example.test", "company": "Acme", "status": "Open"},
                    {"row_number": 13, "email": "nina@example.test", "company": "Globex", "status": "Open"},
                    {"row_number": 14, "email": "ana@example.test", "company": "Initech", "status": "Open"},
                ],
                "next_step": "Call the exact Google Sheets tool requested by the user; do not inspect eval bookkeeping tables.",
            },
        }

    @staticmethod
    def _sqlite_mock_success():
        return {
            "status": "ok",
            "tool": "sqlite_batch",
            "message": "Mocked SQLite result for deterministic common-use-case eval.",
            "content": {
                "ok": True,
                "tables": ["leads", "accounts", "contacts", "__tool_results", "__files"],
                "columns": ["company", "email", "priority", "status", "value", "path", "size_bytes", "mime_type"],
                "rows": [
                    {
                        "company": "Acme",
                        "email": "lead-a@example.test",
                        "priority": "high",
                        "status": "open",
                        "value": 90000,
                    },
                    {
                        "company": "Globex",
                        "email": "lead-b@example.test",
                        "priority": "medium",
                        "status": "open",
                        "value": 75000,
                    },
                    {
                        "company": "Initech",
                        "email": "lead-c@example.test",
                        "priority": "low",
                        "status": "contacted",
                        "value": 25000,
                    },
                    {
                        "path": "/exports/report.pdf",
                        "size_bytes": 1024,
                        "mime_type": "application/pdf",
                    },
                ],
                "next_step": "The requested eval fixture data exists; continue with the user-requested tool.",
            },
        }

    @staticmethod
    def _revenue_sqlite_mock_success():
        return {
            "status": "ok",
            "tool": "sqlite_batch",
            "message": "Mocked SQLite result for deterministic revenue chart eval.",
            "content": {
                "ok": True,
                "tables": ["revenue_data", "__tool_results", "__files"],
                "columns": ["month", "revenue"],
                "rows": [
                    {"month": "Jan", "revenue": 120},
                    {"month": "Feb", "revenue": 135},
                    {"month": "Mar", "revenue": 150},
                    {"month": "Apr", "revenue": 142},
                    {"month": "May", "revenue": 165},
                    {"month": "Jun", "revenue": 180},
                ],
                "next_step": "Revenue data is ready; call create_chart next with a query over revenue_data.",
            },
        }

    def _build_mock_config(self):
        case = self.case
        forbidden_tools = case.forbidden_tool_names()
        accepted_tools = self._accepted_expected_tool_names()
        mocked_tools = [
            *accepted_tools,
            *[
                tool_name
                for tool_name in case.allowed_preamble_tool_names()
                if is_eval_synthetic_tool_name(tool_name) or tool_name in {"http_request", "read_file", "sqlite_batch"}
            ],
        ]
        mock_config = {tool_name: self._mock_for_tool(tool_name) for tool_name in mocked_tools}
        for tool_name in forbidden_tools:
            mock_config[tool_name] = {
                "status": "error",
                "message": f"{tool_name} is the wrong tool for this common-use-case eval.",
            }
        return mock_config

    def _accepted_expected_tool_names(self):
        accepted = []
        for tool_name in self.case.expected_tool_names():
            accepted.extend(self.case.accepted_tool_names_for_expected_tool(tool_name))
        return list(dict.fromkeys(accepted))

    @staticmethod
    def _agent_config_field_for_expected_tool(tool_name):
        if tool_name == "update_schedule":
            return "schedule"
        if tool_name == "update_charter":
            return "charter"
        return None

    def _agent_config_field_for_expected_call(self, tool_name):
        config_field = self._agent_config_field_for_expected_tool(tool_name)
        if config_field:
            return config_field
        if self.case.category == "monitoring" and tool_name == "sqlite_batch":
            return "charter" if "charter" in self.case.slug else "schedule"
        return None

    def _accepts_sqlite_config_update(self):
        return any(
            "sqlite_batch" in self.case.accepted_tool_names_for_expected_tool(tool_name)
            and self._agent_config_field_for_expected_call(tool_name)
            for tool_name in self.case.expected_tool_names()
        )

    def _uses_discoverable_eval_tool(self):
        return any(
            is_eval_synthetic_tool_name(tool_name)
            for tool_name in self._accepted_expected_tool_names()
        )

    def _expected_tool_condition(self, tool_name):
        condition = {"tool_name": tool_name}
        alternatives = self.case.expected_tool_alternatives(tool_name)
        if alternatives:
            condition["alternatives"] = alternatives
        config_field = self._agent_config_field_for_expected_call(tool_name)
        if config_field:
            condition["agent_config_field"] = config_field
        if self.case.expected_params and len(self.case.expected_tools) == 1:
            condition["params"] = self.case.expected_params
        return condition

    def _build_eval_stop_policy(self):
        case = self.case
        expected_conditions = []
        if case.plan_expected:
            expected_conditions.append({"tool_name": UPDATE_PLAN_TOOL_NAME})
        for tool_name in case.expected_tool_names():
            expected_conditions.append(self._expected_tool_condition(tool_name))

        stop_on_tool_names = list(case.forbidden_tool_names())
        allowed_tool_names = set(self._accepted_expected_tool_names())
        allowed_tool_names.add(UPDATE_PLAN_TOOL_NAME)
        allowed_tool_names.update(case.allowed_preamble_tool_names())
        if self._uses_discoverable_eval_tool():
            allowed_tool_names.add("search_tools")
        policy = {
            "ignore_sqlite_agent_config_mutations": not self._accepts_sqlite_config_update(),
            "ignore_sqlite_eval_bookkeeping_reads": "sqlite_batch" not in self._accepted_expected_tool_names(),
            "ignored_tool_names": case.ignored_tool_names(),
            "allowed_tool_names": list(allowed_tool_names),
            "accepted_tool_alternatives": {
                tool_name: list(alternatives)
                for tool_name, alternatives in case.accepted_tool_alternatives.items()
            },
            "stop_on_tool_names": stop_on_tool_names,
            "stop_on_unexpected_relevant_tool": True,
        }
        if case.stop_after_success:
            policy["stop_when_all_seen"] = expected_conditions
        return policy

    def run(self, run_id, agent_id):
        case = self.case
        expected_tools = case.expected_tool_names()
        forbidden_tools = case.forbidden_tool_names()
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
        self._seed_prior_processing_run(agent_id)
        tool_names = [*self._accepted_expected_tool_names(), *forbidden_tools]
        synthetic_tool_names = [
            tool_name
            for tool_name in [*tool_names, *case.allowed_preamble_tool_names(), *case.eval_synthetic_tools]
            if is_eval_synthetic_tool_name(tool_name)
        ]
        self._enable_builtin_tools(
            agent_id,
            [tool_name for tool_name in tool_names if tool_name not in synthetic_tool_names],
        )
        if "create_custom_tool" in self._accepted_expected_tool_names():
            self._enable_sandbox_tool_visibility(agent_id)
        self._enable_eval_synthetic_tools(agent_id, list(dict.fromkeys(synthetic_tool_names)))

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                case.prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                mock_config=self._build_mock_config(),
                eval_stop_policy=self._build_eval_stop_policy(),
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected and processing completed.",
            artifacts={"message": inbound},
        )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_plan_policy",
        )
        plan_activity_calls = get_plan_activity_calls_for_run(run_id, after=inbound.timestamp)
        if case.plan_expected:
            if plan_activity_calls:
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.PASSED,
                    task_name="verify_plan_policy",
                    observed_summary=f"Agent performed plan activity via {plan_activity_calls[0].tool_name}.",
                    artifacts={"step": plan_activity_calls[0].step},
                )
            else:
                self.record_task_result(
                    run_id,
                    None,
                    EvalRunTask.Status.FAILED,
                    task_name="verify_plan_policy",
                    observed_summary="Expected plan activity, but saw no update_plan.",
                )
        elif plan_activity_calls:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_plan_policy",
                observed_summary=f"Plan activity was optional; saw {plan_activity_calls[0].tool_name}.",
                artifacts={"step": plan_activity_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_plan_policy",
                observed_summary="No update_plan occurred for this simple task.",
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_expected_tool_usage",
        )
        candidate_calls = (
            get_tool_calls_for_run(run_id, after=inbound.timestamp)
            if self._accepts_sqlite_config_update()
            else get_common_use_case_tool_calls_for_run(
                run_id,
                after=inbound.timestamp,
                include_sqlite_eval_bookkeeping_reads="sqlite_batch" in self._accepted_expected_tool_names(),
            )
        )
        expected_calls = [
            call
            for call in candidate_calls
            if call.tool_name in self._accepted_expected_tool_names()
        ]
        missing_expected_tools = [
            tool_name
            for tool_name in expected_tools
            if not any(self._call_satisfies_expected_tool(call, tool_name) for call in expected_calls)
        ]
        if not missing_expected_tools:
            seen_tools = [call.tool_name for call in expected_calls]
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_expected_tool_usage",
                observed_summary=f"Agent used expected tool(s): {seen_tools}.",
                artifacts={"step": expected_calls[0].step},
            )
        else:
            seen_tools = [call.tool_name for call in expected_calls]
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_expected_tool_usage",
                observed_summary=(
                    f"Missing expected tool(s) {missing_expected_tools}; accepted tool(s) "
                    f"{self._accepted_expected_tool_names()} with params {case.expected_params or '{}'}; "
                    f"saw {seen_tools}."
                ),
                artifacts={"step": expected_calls[0].step} if expected_calls else {},
            )

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.RUNNING,
            task_name="verify_forbidden_tool_absence",
        )
        forbidden_calls = get_common_use_case_tool_calls_for_run(
            run_id,
            after=inbound.timestamp,
            tool_names=forbidden_tools,
        )
        if forbidden_calls:
            seen_tools = [call.tool_name for call in forbidden_calls]
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_forbidden_tool_absence",
                observed_summary=f"Agent used forbidden tool(s): {seen_tools}.",
                artifacts={"step": forbidden_calls[0].step},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_forbidden_tool_absence",
                observed_summary="Agent avoided forbidden tool(s).",
            )

    @staticmethod
    def _calls_match_expected_params(calls, expected_params):
        if not expected_params:
            return True
        for call in calls:
            params = call.tool_params or {}
            if all(params.get(key) == value for key, value in expected_params.items()):
                return True
        return False

    def _call_satisfies_expected_tool(self, call, expected_tool_name):
        if call.tool_name not in self.case.accepted_tool_names_for_expected_tool(expected_tool_name):
            return False
        if (
            self.case.expected_params
            and len(self.case.expected_tools) == 1
            and not self._calls_match_expected_params([call], self.case.expected_params)
        ):
            return False
        config_field = self._agent_config_field_for_expected_call(expected_tool_name)
        if config_field and call.tool_name == "sqlite_batch":
            return sqlite_batch_mutates_agent_config_field(call, config_field)
        return True


def _common_use_case_scenario_class(case):
    class _CommonUseCaseScenario(CommonUseCaseToolChoiceScenario):
        slug = case.slug
        description = f"Common {case.category} request should choose the expected deterministic tool."
        category = case.category
        tags = (
            "agent_behavior",
            "micro",
            "tool_choice",
            "common_use_case",
            case.category,
            "planning_expected" if case.plan_expected else "direct_tool",
        )

    _CommonUseCaseScenario.case = case
    _CommonUseCaseScenario.__name__ = "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    return _CommonUseCaseScenario


for common_use_case in COMMON_USE_CASE_EVAL_CASES:
    ScenarioRegistry.register(_common_use_case_scenario_class(common_use_case)())
