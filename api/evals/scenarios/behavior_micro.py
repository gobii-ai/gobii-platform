from dataclasses import dataclass, field

from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import ScenarioRegistry, register_scenario
from api.evals.stop_policy import (
    sqlite_batch_is_only_planning_state_mutation,
    sqlite_batch_mutates_agent_config_field,
    sqlite_batch_mutates_planning_state,
)
from api.models import (
    EvalRunTask,
    PersistentAgent,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    PersistentAgentToolCall,
)

PLANNING_FIRST_TURN_ASKS_BOUNDED_QUESTIONS = "planning_first_turn_asks_bounded_questions"
PLANNING_CLEAR_TASK_ENDS_PLANNING_FIRST = "planning_clear_task_ends_planning_first"
PLANNING_EXECUTE_REQUEST_STAYS_IN_PLANNING = "planning_execute_request_stays_in_planning"
PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES = "planning_no_direct_schedule_or_config_updates"

TOOL_CHOICE_EXACT_JSON_URL_USES_HTTP_REQUEST = "tool_choice_exact_json_url_uses_http_request"
TOOL_CHOICE_CSV_DELIVERABLE_USES_CREATE_CSV = "tool_choice_csv_deliverable_uses_create_csv"
TOOL_CHOICE_PDF_DELIVERABLE_USES_CREATE_PDF = "tool_choice_pdf_deliverable_uses_create_pdf"
TOOL_CHOICE_MISSING_RECIPIENT_USES_HUMAN_INPUT = "tool_choice_missing_recipient_uses_human_input"

UPDATE_PLAN_TOOL_NAME = "update_plan"
UPDATE_PLAN_POLICY_EXPECT = "expect"
UPDATE_PLAN_POLICY_FORBID = "forbid"
UPDATE_PLAN_POLICIES = {
    UPDATE_PLAN_POLICY_EXPECT,
    UPDATE_PLAN_POLICY_FORBID,
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
            stop_after_success=stop_after_success,
        )
        definition.validate()
        return definition

    @property
    def update_plan_policy(self):
        return UPDATE_PLAN_POLICY_EXPECT if self.plan_expected else UPDATE_PLAN_POLICY_FORBID

    def validate(self):
        if not self.slug:
            raise ValueError("Common use case eval is missing slug.")
        if not self.category:
            raise ValueError(f"{self.slug} is missing category.")
        if not self.prompt:
            raise ValueError(f"{self.slug} is missing prompt.")
        if not self.expected_tools:
            raise ValueError(f"{self.slug} must declare expected_tools.")
        if UPDATE_PLAN_TOOL_NAME in self.expected_tools or UPDATE_PLAN_TOOL_NAME in self.forbidden_tools:
            raise ValueError(f"{self.slug} must use plan_expected instead of tool lists for update_plan.")

    def expected_tool_names(self):
        return list(self.expected_tools)

    def forbidden_tool_names(self):
        return list(self.forbidden_tools)

    def ignored_tool_names(self):
        return list(dict.fromkeys([*IGNORED_FIRST_ACTION_TOOL_NAMES, *self.ignored_tools]))

    def allowed_preamble_tool_names(self):
        return list(dict.fromkeys([*IGNORED_FIRST_ACTION_TOOL_NAMES, *self.allowed_preamble_tools]))

    def expected_tool_alternatives(self, tool_name):
        return list(self.accepted_tool_alternatives.get(tool_name, ()))

    def accepted_tool_names_for_expected_tool(self, tool_name):
        return [tool_name, *self.expected_tool_alternatives(tool_name)]


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
    {"slug": "common_use_case_020_search_reddit_mentions", "category": "web_research", "prompt": "Search the web for Reddit mentions of a gut health supplement and summarize sentiment.", "expected_tools": ["mcp_brightdata_search_engine"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_021_scrape_known_article", "category": "web_scrape", "prompt": "Scrape https://news.example.test/article-42 and return the headline.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_022_scrape_known_blog", "category": "web_scrape", "prompt": "Scrape https://blog.example.test/q2-roadmap and return the author name.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_023_scrape_known_pricing_page", "category": "web_scrape", "prompt": "Scrape https://vendor.example.test/pricing and return the starter plan price.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_024_scrape_known_docs_page", "category": "web_scrape", "prompt": "Scrape https://docs.example.test/api/auth and return the required header name.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_025_scrape_known_permit_page", "category": "web_scrape", "prompt": "Scrape https://borough.example.test/permits/zoning and return the filing fee.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_026_scrape_known_jobs_page", "category": "web_scrape", "prompt": "Scrape https://careers.example.test/jobs and return the first listed role.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_027_scrape_known_changelog", "category": "web_scrape", "prompt": "Scrape https://app.example.test/changelog and return the latest release date.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_028_scrape_known_directory", "category": "web_scrape", "prompt": "Scrape https://directory.example.test/vendors and return the first vendor name.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_029_scrape_known_support_page", "category": "web_scrape", "prompt": "Scrape https://support.example.test/status and return the support email.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_030_scrape_known_event_page", "category": "web_scrape", "prompt": "Scrape https://events.example.test/summit and return the venue.", "expected_tools": ["mcp_brightdata_scrape_as_markdown"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_031_linkedin_person_profile", "category": "lead_sourcing", "prompt": "Find the LinkedIn profile for Jordan Lee at Acme AI and return title and location.", "expected_tools": ["mcp_brightdata_web_data_linkedin_person_profile"], "forbidden_tools": ["spawn_web_task"], "plan_expected": True},
    {"slug": "common_use_case_032_linkedin_company_profile", "category": "lead_sourcing", "prompt": "Look up the LinkedIn company profile for Acme AI and return industry and size.", "expected_tools": ["mcp_brightdata_web_data_linkedin_company_profile"], "forbidden_tools": ["spawn_web_task"], "plan_expected": True},
    {"slug": "common_use_case_033_linkedin_job_listings", "category": "lead_sourcing", "prompt": "Find LinkedIn job listings for Acme AI and return two open role titles.", "expected_tools": ["mcp_brightdata_web_data_linkedin_job_listings"], "forbidden_tools": ["spawn_web_task"], "plan_expected": True},
    {"slug": "common_use_case_034_linkedin_people_search", "category": "lead_sourcing", "prompt": "Search LinkedIn for product leaders at Acme AI and return three names.", "expected_tools": ["mcp_brightdata_web_data_linkedin_people_search"], "forbidden_tools": ["spawn_web_task"], "plan_expected": True},
    {"slug": "common_use_case_035_linkedin_posts", "category": "lead_sourcing", "prompt": "Find recent LinkedIn posts from Acme AI and summarize the latest post.", "expected_tools": ["mcp_brightdata_web_data_linkedin_posts"], "forbidden_tools": ["spawn_web_task"], "plan_expected": True},
    {"slug": "common_use_case_036_apollo_contacts", "category": "lead_sourcing", "prompt": "Search Apollo for VP Sales contacts at healthcare SaaS companies in Boston.", "expected_tools": ["apollo_io-search-contacts"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": True},
    {"slug": "common_use_case_037_apollo_accounts", "category": "lead_sourcing", "prompt": "Search Apollo for cybersecurity accounts with 50-200 employees in Austin.", "expected_tools": ["apollo_io-search-accounts"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": True},
    {"slug": "common_use_case_038_apollo_enrich_person", "category": "lead_sourcing", "prompt": "Enrich the Apollo profile for pat@example.test and return company and title.", "expected_tools": ["apollo_io-people-enrichment"], "forbidden_tools": ["mcp_brightdata_search_engine", "spawn_web_task"], "plan_expected": True},
    {"slug": "common_use_case_039_amazon_product", "category": "commerce_research", "prompt": "Get Amazon product data for ASIN B000TEST01 and return rating and price.", "expected_tools": ["mcp_brightdata_web_data_amazon_product"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_040_instagram_profile", "category": "social_research", "prompt": "Get Instagram profile data for examplebrand and return follower count.", "expected_tools": ["mcp_brightdata_web_data_instagram_profiles"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_041_reddit_posts", "category": "social_research", "prompt": "Fetch Reddit posts about ExampleApp and summarize the top complaint.", "expected_tools": ["mcp_brightdata_web_data_reddit_posts"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_042_google_maps_reviews", "category": "local_research", "prompt": "Fetch Google Maps reviews for Example Cafe and summarize the rating themes.", "expected_tools": ["mcp_brightdata_web_data_google_maps_reviews"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_043_yahoo_finance_business", "category": "finance_research", "prompt": "Fetch Yahoo Finance business data for MSFT and return market cap.", "expected_tools": ["mcp_brightdata_web_data_yahoo_finance_business"], "forbidden_tools": ["spawn_web_task"], "plan_expected": False},
    {"slug": "common_use_case_044_linkedin_company_jobs", "category": "lead_sourcing", "prompt": "Find LinkedIn job listings for a fintech company and return remote roles.", "expected_tools": ["mcp_brightdata_web_data_linkedin_job_listings"], "forbidden_tools": ["spawn_web_task"], "plan_expected": True},
    {"slug": "common_use_case_045_linkedin_candidate_search", "category": "lead_sourcing", "prompt": "Search LinkedIn for senior backend candidates in Toronto with Python experience.", "expected_tools": ["mcp_brightdata_web_data_linkedin_people_search"], "forbidden_tools": ["spawn_web_task"], "plan_expected": True},
    {"slug": "common_use_case_046_sheets_read_range", "category": "sheets", "prompt": "Read A1:D20 from the Leads worksheet in spreadsheet sheet-123.", "expected_tools": ["google_sheets-get-values-in-range"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_047_sheets_find_row", "category": "sheets", "prompt": "Find the row in spreadsheet sheet-123 where email equals ana@example.test.", "expected_tools": ["google_sheets-find-row"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_048_sheets_add_single_row", "category": "sheets", "prompt": "Add one row to the Leads sheet for Acme, high priority, owner Sam.", "expected_tools": ["google_sheets-add-single-row"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_049_sheets_add_multiple_rows", "category": "sheets", "prompt": "Add three prospect rows to the Leads worksheet in spreadsheet sheet-123.", "expected_tools": ["google_sheets-add-multiple-rows"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_050_sheets_update_cell", "category": "sheets", "prompt": "Update cell C8 in spreadsheet sheet-123 to Qualified.", "expected_tools": ["google_sheets-update-cell"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_051_sheets_update_row", "category": "sheets", "prompt": "Update the lead row for Globex with status Contacted.", "expected_tools": ["google_sheets-update-row"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_052_sheets_update_multiple_rows", "category": "sheets", "prompt": "Update three rows in the pipeline sheet to mark follow_up_due as today.", "expected_tools": ["google_sheets-update-multiple-rows"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_053_sheets_upsert_row", "category": "sheets", "prompt": "Upsert the account row keyed by domain example.test with status active.", "expected_tools": ["google_sheets-upsert-row"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_054_sheets_list_worksheets", "category": "sheets", "prompt": "List worksheets in spreadsheet sheet-123 and return their titles.", "expected_tools": ["google_sheets-list-worksheets"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_055_sheets_info", "category": "sheets", "prompt": "Get spreadsheet info for sheet-123 and report the spreadsheet title.", "expected_tools": ["google_sheets-get-spreadsheet-info"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_056_sheets_create_spreadsheet", "category": "sheets", "prompt": "Create a spreadsheet named Q2 Lead Tracker with a Leads worksheet.", "expected_tools": ["google_sheets-create-spreadsheet"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_057_sheets_read_rows", "category": "sheets", "prompt": "Read the first 10 rows from the Tasks worksheet in spreadsheet sheet-123.", "expected_tools": ["google_sheets-read-rows"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_058_sheets_get_by_id", "category": "sheets", "prompt": "Open spreadsheet sheet-123 by id and return its name.", "expected_tools": ["google_sheets-get-spreadsheet-by-id"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_059_sheets_current_user", "category": "sheets", "prompt": "Check the connected Google Sheets user before editing the tracker.", "expected_tools": ["google_sheets-get-current-user"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_060_sheets_append_rows", "category": "sheets", "prompt": "Append two new rows to the Research worksheet in spreadsheet sheet-123.", "expected_tools": ["google_sheets-add-rows"], "forbidden_tools": ["sqlite_batch"], "plan_expected": False},
    {"slug": "common_use_case_061_send_summary_email", "category": "outbound", "prompt": "Email ana@example.test a three-sentence summary of today's pipeline changes.", "expected_tools": ["send_email"], "forbidden_tools": ["send_sms"], "accepted_tool_alternatives": {"send_email": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_062_send_attachment_email", "category": "outbound", "prompt": "Email pat@example.test that the report is attached at $[/exports/report.pdf].", "expected_tools": ["send_email"], "forbidden_tools": ["send_sms"], "accepted_tool_alternatives": {"send_email": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_063_send_followup_email", "category": "outbound", "prompt": "Send a polite follow-up email to lee@example.test about the demo times.", "expected_tools": ["send_email"], "forbidden_tools": ["send_sms"], "accepted_tool_alternatives": {"send_email": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_064_send_digest_email", "category": "outbound", "prompt": "Email ops@example.test the daily incident digest with three bullet points.", "expected_tools": ["send_email"], "forbidden_tools": ["send_sms"], "accepted_tool_alternatives": {"send_email": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_065_send_status_sms", "category": "outbound", "prompt": "Text +15555550123 that the build finished successfully.", "expected_tools": ["send_sms"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"send_sms": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_066_send_meeting_sms", "category": "outbound", "prompt": "Send an SMS to +15555550123 saying the meeting moved to 3pm.", "expected_tools": ["send_sms"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"send_sms": ["request_contact_permission"]}, "plan_expected": False},
    {"slug": "common_use_case_067_request_contact_email_permission", "category": "outbound", "prompt": "Ask permission to email new-contact@example.test about the partnership intro.", "expected_tools": ["request_contact_permission"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_068_request_sms_permission", "category": "outbound", "prompt": "Ask permission before texting +15555550123 about the urgent outage.", "expected_tools": ["request_contact_permission"], "forbidden_tools": ["send_sms"], "plan_expected": False},
    {"slug": "common_use_case_069_secure_api_key_request", "category": "credentials", "prompt": "Request the missing STRIPE_API_KEY secret so you can call the Stripe API.", "expected_tools": ["secure_credentials_request"], "allowed_preamble_tools": ["send_chat_message"], "plan_expected": False},
    {"slug": "common_use_case_070_secure_login_request", "category": "credentials", "prompt": "Request the portal password for https://vendor.example.test before logging in.", "expected_tools": ["secure_credentials_request"], "allowed_preamble_tools": ["send_chat_message"], "plan_expected": False},
    {"slug": "common_use_case_071_create_leads_csv", "category": "files", "prompt": "Create /exports/leads.csv with columns company,email,priority and two rows.", "expected_tools": ["create_csv"], "forbidden_tools": ["create_file"], "plan_expected": False},
    {"slug": "common_use_case_072_create_jobs_csv", "category": "files", "prompt": "Create /exports/jobs.csv with columns title,company,url and three rows.", "expected_tools": ["create_csv"], "forbidden_tools": ["create_file"], "plan_expected": False},
    {"slug": "common_use_case_073_create_status_pdf", "category": "files", "prompt": "Create a one-page PDF at /exports/status.pdf with wins, risks, and next steps.", "expected_tools": ["create_pdf"], "forbidden_tools": ["create_file"], "plan_expected": False},
    {"slug": "common_use_case_074_create_permit_pdf", "category": "files", "prompt": "Create a PDF at /exports/permit-summary.pdf summarizing zoning permit requirements.", "expected_tools": ["create_pdf"], "forbidden_tools": ["create_file"], "plan_expected": False},
    {"slug": "common_use_case_075_create_markdown_file", "category": "files", "prompt": "Create /exports/notes.md with a short meeting summary and action items.", "expected_tools": ["create_file"], "forbidden_tools": ["create_csv", "create_pdf"], "plan_expected": False},
    {"slug": "common_use_case_076_create_json_file", "category": "files", "prompt": "Create /exports/config.json containing feature_enabled true and retry_count 3.", "expected_tools": ["create_file"], "forbidden_tools": ["create_csv", "create_pdf"], "plan_expected": False},
    {"slug": "common_use_case_077_create_bar_chart", "category": "files", "prompt": "Create a bar chart of weekly leads with values 12, 18, 9, and 24.", "expected_tools": ["create_chart"], "forbidden_tools": ["create_csv"], "plan_expected": False},
    {"slug": "common_use_case_078_create_line_chart", "category": "files", "prompt": "Create a line chart for daily signups with values 4, 7, 5, 11, and 13.", "expected_tools": ["create_chart"], "forbidden_tools": ["create_csv"], "plan_expected": False},
    {"slug": "common_use_case_079_create_report_with_chart", "category": "files", "prompt": "Create a chart showing revenue by month and prepare it for a PDF report.", "expected_tools": ["create_chart"], "forbidden_tools": ["send_email"], "plan_expected": True},
    {"slug": "common_use_case_080_read_uploaded_file", "category": "files", "prompt": "Read /uploads/brief.txt and summarize the three requested edits.", "expected_tools": ["read_file"], "forbidden_tools": ["mcp_brightdata_search_engine"], "plan_expected": False},
    {"slug": "common_use_case_081_sqlite_create_table", "category": "database", "prompt": "Create a SQLite table leads with columns company, email, and priority.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-add-single-row"], "plan_expected": False},
    {"slug": "common_use_case_082_sqlite_insert_rows", "category": "database", "prompt": "Insert two lead rows into the SQLite leads table.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-add-single-row"], "plan_expected": False},
    {"slug": "common_use_case_083_sqlite_query_counts", "category": "database", "prompt": "Query SQLite for lead counts grouped by priority.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-get-values-in-range"], "plan_expected": False},
    {"slug": "common_use_case_084_sqlite_update_status", "category": "database", "prompt": "Update SQLite lead Acme to status contacted.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-update-row"], "plan_expected": False},
    {"slug": "common_use_case_085_sqlite_join_tables", "category": "database", "prompt": "Query SQLite to join accounts and contacts by account_id.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-get-values-in-range"], "plan_expected": False},
    {"slug": "common_use_case_086_sqlite_export_query_csv", "category": "database", "prompt": "Run a SQLite query for open leads, then create a CSV export.", "expected_tools": ["sqlite_batch", "create_csv"], "forbidden_tools": ["google_sheets-get-values-in-range"], "plan_expected": True},
    {"slug": "common_use_case_087_sqlite_clean_duplicates", "category": "database", "prompt": "Remove duplicate emails from the SQLite contacts table.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-update-multiple-rows"], "plan_expected": False},
    {"slug": "common_use_case_088_sqlite_add_index", "category": "database", "prompt": "Add a SQLite index on contacts email for faster lookup.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["google_sheets-update-cell"], "plan_expected": False},
    {"slug": "common_use_case_089_enable_database", "category": "database", "prompt": "Enable the database so you can store a lead tracker for this agent.", "expected_tools": ["enable_database"], "forbidden_tools": ["google_sheets-create-spreadsheet"], "plan_expected": False},
    {"slug": "common_use_case_090_sqlite_summarize_messages", "category": "database", "prompt": "Query SQLite message history and summarize the last five user requests.", "expected_tools": ["sqlite_batch"], "forbidden_tools": ["mcp_brightdata_search_engine"], "plan_expected": False},
    {"slug": "common_use_case_091_schedule_daily_digest", "category": "monitoring", "prompt": "Set a daily 9am ET schedule for a competitor pricing digest.", "expected_tools": ["update_schedule"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"update_schedule": ["sqlite_batch"]}, "plan_expected": True},
    {"slug": "common_use_case_092_schedule_hourly_monitor", "category": "monitoring", "prompt": "Set an hourly schedule to monitor the support status page.", "expected_tools": ["update_schedule"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"update_schedule": ["sqlite_batch"]}, "plan_expected": True},
    {"slug": "common_use_case_093_schedule_weekly_report", "category": "monitoring", "prompt": "Set a Monday 8am schedule for a weekly pipeline report.", "expected_tools": ["update_schedule"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"update_schedule": ["sqlite_batch"]}, "plan_expected": True},
    {"slug": "common_use_case_094_update_agent_charter", "category": "monitoring", "prompt": "Update your charter to monitor AI funding news and summarize notable deals.", "expected_tools": ["update_charter"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"update_charter": ["sqlite_batch"]}, "plan_expected": True},
    {"slug": "common_use_case_095_request_research_scope", "category": "human_input", "prompt": "Ask me which target account segment to research before starting the work.", "expected_tools": ["request_human_input"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_096_schedule_price_alert", "category": "monitoring", "prompt": "Set a daily schedule to check the BTC price and alert only if it moves 5 percent.", "expected_tools": ["update_schedule"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"update_schedule": ["sqlite_batch"]}, "plan_expected": True},
    {"slug": "common_use_case_097_schedule_permit_check", "category": "monitoring", "prompt": "Set a weekday schedule to check the borough permit page for updates.", "expected_tools": ["update_schedule"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"update_schedule": ["sqlite_batch"]}, "plan_expected": True},
    {"slug": "common_use_case_098_update_charter_sourcing", "category": "monitoring", "prompt": "Update your charter to source three qualified backend candidates each weekday.", "expected_tools": ["update_charter"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"update_charter": ["sqlite_batch"]}, "plan_expected": True},
    {"slug": "common_use_case_099_request_monitoring_scope", "category": "human_input", "prompt": "Ask which competitors and update types matter before setting up monitoring.", "expected_tools": ["request_human_input"], "forbidden_tools": ["send_email"], "plan_expected": False},
    {"slug": "common_use_case_100_schedule_daily_email_digest", "category": "monitoring", "prompt": "Set a daily schedule to prepare a concise email digest of market news.", "expected_tools": ["update_schedule"], "forbidden_tools": ["send_email"], "accepted_tool_alternatives": {"update_schedule": ["sqlite_batch"]}, "plan_expected": True},
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
    PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES,
]

TOOL_CHOICE_MICRO_SCENARIO_SLUGS = [
    TOOL_CHOICE_EXACT_JSON_URL_USES_HTTP_REQUEST,
    TOOL_CHOICE_CSV_DELIVERABLE_USES_CREATE_CSV,
    TOOL_CHOICE_PDF_DELIVERABLE_USES_CREATE_PDF,
    TOOL_CHOICE_MISSING_RECIPIENT_USES_HUMAN_INPUT,
    *COMMON_USE_CASE_MICRO_SCENARIO_SLUGS,
]

BEHAVIOR_MICRO_SCENARIO_SLUGS = PLANNING_MICRO_SCENARIO_SLUGS + TOOL_CHOICE_MICRO_SCENARIO_SLUGS

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

PLANNING_MUTATION_TOOL_NAMES = {
    "update_schedule",
    "update_charter",
    "update_plan",
}

IGNORED_FIRST_ACTION_TOOL_NAMES = {
    "send_chat_message",
    "sleep_until_next_trigger",
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


def get_common_use_case_tool_calls_for_run(run_id, *, after=None, tool_names=None):
    return [
        call
        for call in get_tool_calls_for_run(run_id, after=after, tool_names=tool_names)
        if not sqlite_batch_is_only_planning_state_mutation(call)
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
    def _set_planning_state(self, agent_id, state):
        PersistentAgent.objects.filter(id=agent_id).update(planning_state=state)

    def _enable_builtin_tools(self, agent_id, tool_names):
        agent = PersistentAgent.objects.get(id=agent_id)
        for tool_name in tool_names:
            mark_tool_enabled_without_discovery(agent, tool_name)

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
            if call.id in seen_ids:
                continue
            seen_ids.add(call.id)
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
    description = "Planning mode should welcome the user, ask 1-3 tracked questions with options, and not start work."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_welcome_message", assertion_type="manual"),
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

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_welcome_message")
        outbound = PersistentAgentMessage.objects.filter(
            owner_agent_id=agent_id,
            conversation_id=inbound.conversation_id,
            is_outbound=True,
            timestamp__gt=inbound.timestamp,
        ).order_by("timestamp").first()
        if outbound:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.PASSED,
                task_name="verify_welcome_message",
                observed_summary="Agent sent an outbound welcome/planning message.",
                artifacts={"message": outbound},
            )
        else:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_welcome_message",
                observed_summary="No outbound welcome/planning message was sent.",
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
class PlanningClearTaskEndsPlanningFirstScenario(BehaviorMicroScenario):
    slug = PLANNING_CLEAR_TASK_ENDS_PLANNING_FIRST
    description = "A clear task in planning mode should call end_planning before doing substantive work."
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
                    "Set up a daily 9am ET digest of SEC enforcement press releases. "
                    "Summarize new actions, include links, and skip days with no updates."
                ),
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
class PlanningNoDirectScheduleOrConfigUpdatesScenario(BehaviorMicroScenario):
    slug = PLANNING_NO_DIRECT_SCHEDULE_OR_CONFIG_UPDATES
    description = "Planning mode should not update schedule, charter, or runtime plan before end_planning."
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
class ToolChoiceExactJsonUrlUsesHttpRequestScenario(BehaviorMicroScenario):
    slug = TOOL_CHOICE_EXACT_JSON_URL_USES_HTTP_REQUEST
    description = "An exact JSON API URL should be fetched with http_request, not search or browser tools."
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_http_request_first", assertion_type="manual"),
        ScenarioTask(name="verify_exact_url", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
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
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_create_csv", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
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
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_create_pdf", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)
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
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_human_input", assertion_type="manual"),
        ScenarioTask(name="verify_no_send_email", assertion_type="manual"),
    ]

    def run(self, run_id, agent_id):
        self._set_planning_state(agent_id, PersistentAgent.PlanningState.SKIPPED)

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
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_plan_policy", assertion_type="manual"),
        ScenarioTask(name="verify_expected_tool_usage", assertion_type="manual"),
        ScenarioTask(name="verify_forbidden_tool_absence", assertion_type="manual"),
    ]
    case = None

    @staticmethod
    def _mock_success(tool_name):
        return {
            "status": "ok",
            "tool": tool_name,
            "message": f"Mocked {tool_name} result for deterministic common-use-case eval.",
            "content": {"ok": True},
        }

    def _build_mock_config(self):
        case = self.case
        expected_tools = case.expected_tool_names()
        forbidden_tools = case.forbidden_tool_names()
        accepted_tools = self._accepted_expected_tool_names()
        mock_config = {tool_name: self._mock_success(tool_name) for tool_name in accepted_tools}
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

    def _accepts_sqlite_config_update(self):
        return any(
            "sqlite_batch" in self.case.expected_tool_alternatives(tool_name)
            and self._agent_config_field_for_expected_tool(tool_name)
            for tool_name in self.case.expected_tool_names()
        )

    def _expected_tool_condition(self, tool_name):
        condition = {"tool_name": tool_name}
        alternatives = self.case.expected_tool_alternatives(tool_name)
        if alternatives:
            condition["alternatives"] = alternatives
        config_field = self._agent_config_field_for_expected_tool(tool_name)
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
        if not case.plan_expected:
            stop_on_tool_names.append(UPDATE_PLAN_TOOL_NAME)

        allowed_tool_names = set(self._accepted_expected_tool_names())
        if case.plan_expected:
            allowed_tool_names.add(UPDATE_PLAN_TOOL_NAME)
        allowed_tool_names.update(case.allowed_preamble_tool_names())
        policy = {
            "ignore_sqlite_agent_config_mutations": not self._accepts_sqlite_config_update(),
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
        self._enable_builtin_tools(agent_id, [*self._accepted_expected_tool_names(), *forbidden_tools])

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
                EvalRunTask.Status.FAILED,
                task_name="verify_plan_policy",
                observed_summary=f"Unexpected plan activity via {plan_activity_calls[0].tool_name}.",
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
            else get_common_use_case_tool_calls_for_run(run_id, after=inbound.timestamp)
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
        config_field = self._agent_config_field_for_expected_tool(expected_tool_name)
        if config_field and call.tool_name == "sqlite_batch":
            return sqlite_batch_mutates_agent_config_field(call, config_field)
        return True


def _common_use_case_scenario_class(case):
    class _CommonUseCaseScenario(CommonUseCaseToolChoiceScenario):
        slug = case.slug
        description = f"Common {case.category} request should choose the expected deterministic tool."

    _CommonUseCaseScenario.case = case
    _CommonUseCaseScenario.__name__ = "".join(part.title() for part in case.slug.split("_")) + "Scenario"
    return _CommonUseCaseScenario


for common_use_case in COMMON_USE_CASE_EVAL_CASES:
    ScenarioRegistry.register(_common_use_case_scenario_class(common_use_case)())
