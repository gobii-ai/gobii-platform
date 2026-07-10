
from api.evals.registry import ScenarioRegistry
# Import scenarios here to ensure they are registered when the registry is imported elsewhere
from api.evals.scenarios import * # noqa
from api.evals.scenarios.behavior_micro import BEHAVIOR_MICRO_SCENARIO_SLUGS, CHARTER_MEMORY_MICRO_SCENARIO_SLUGS, PLANNING_MICRO_SCENARIO_SLUGS, SCHEDULE_INTENT_MICRO_SCENARIO_SLUGS, TOOL_CHOICE_MICRO_SCENARIO_SLUGS
from api.evals.scenarios.effort_calibration import EFFORT_CALIBRATION_SCENARIO_SLUGS
from api.evals.scenarios.custom_tool_result_contract import CUSTOM_TOOL_RESULT_CONTRACT_SCENARIO_SLUGS, CUSTOM_TOOL_RESULT_CONTRACT_SUITE_SLUG
from api.evals.scenarios.daily_credit_prompt import DAILY_CREDIT_PROMPT_SCENARIO_SLUGS, DAILY_CREDIT_PROMPT_SUITE_SLUG
from api.evals.scenarios.sqlite_tool_results import (
    SQLITE_DEDUPE_REQUERY,
    SQLITE_INTERMEDIATE_WORKING_TABLE,
    SQLITE_ITEM_LINK_REPORT,
    SQLITE_MULTI_RESULT_WEB_SYNTHESIS,
    SQLITE_TOOL_RESULT_SCENARIO_SLUGS,
    SQLITE_TOOL_RESULT_SUITE_SLUG,
)
from api.evals.scenarios.message_quality import MESSAGE_QUALITY_SCENARIO_SLUGS, MESSAGE_QUALITY_SUITE_SLUG
from api.evals.scenarios.google_sheets_native import (
    GOOGLE_SHEETS_NATIVE_APPEND_ROW,
    GOOGLE_SHEETS_NATIVE_CREATE_AND_FORMAT,
    GOOGLE_SHEETS_NATIVE_FIND_SHEET_BY_NAME,
    GOOGLE_SHEETS_NATIVE_MISSING_SELECTED_FILE,
    GOOGLE_SHEETS_NATIVE_READ_RANGE,
    GOOGLE_SHEETS_NATIVE_SCENARIO_SLUGS,
    GOOGLE_SHEETS_NATIVE_SUITE_SLUG,
)
from api.evals.scenarios.apollo_native import (
    APOLLO_NATIVE_CREATE_CONTACT,
    APOLLO_NATIVE_MISSING_CONNECTION,
    APOLLO_NATIVE_PEOPLE_SEARCH,
    APOLLO_NATIVE_PERSON_ENRICHMENT,
    APOLLO_NATIVE_SCENARIO_SLUGS,
    APOLLO_NATIVE_SUITE_SLUG,
)
from api.evals.scenarios.recruitment_sourcing import RECRUITMENT_SOURCING_SCENARIO_SLUGS, RECRUITMENT_SOURCING_SUITE_SLUG
from api.evals.scenarios.hubspot_native import (
    HUBSPOT_NATIVE_CONTACT_SEARCH,
    HUBSPOT_NATIVE_CREATE_CONTACT,
    HUBSPOT_NATIVE_DEAL_UPDATE,
    HUBSPOT_NATIVE_MISSING_CONNECTION,
    HUBSPOT_NATIVE_SCENARIO_SLUGS,
    HUBSPOT_NATIVE_SUITE_SLUG,
)
from api.evals.scenarios.image_generation import IMAGE_GENERATION_SCENARIO_SLUGS, IMAGE_GENERATION_SUITE_SLUG
from api.evals.scenarios.meta_gobii import META_GOBII_REAL_HARNESS_SCENARIO_SLUGS, META_GOBII_REAL_HARNESS_SUITE_SLUG
from api.evals.meta_gobii import META_GOBII_EVAL_SCENARIO_SLUGS, META_GOBII_EVAL_SUITE_SLUG
from api.evals.suites import EvalSuite, register_builtin_suites


CORE_COMMON_USE_CASE_SCENARIO_SLUGS = (
    "common_use_case_001_fetch_inventory_json",
    "common_use_case_011_research_competitor_pricing",
    "common_use_case_021_scrape_known_article",
    "common_use_case_031_linkedin_person_profile",
    "common_use_case_036_apollo_contacts",
    "common_use_case_067_request_contact_email_permission",
    "common_use_case_069_secure_api_key_request",
    "common_use_case_071_create_leads_csv",
    "common_use_case_077_create_bar_chart",
    "common_use_case_080_read_uploaded_file",
    "common_use_case_091_schedule_daily_digest",
    "common_use_case_136_apollo_connect_tool_search",
    "common_use_case_137_slack_connect_tool_search",
)

CORE_EFFORT_SCENARIO_SLUGS = (
    "effort_trivial_answer_stops",
    "effort_simple_lookup_bounded_tools",
    "effort_scheduled_briefing_finishes",
    "effort_defaultable_research_no_question_battery",
    "effort_partial_source_block_reports_and_resumes",
    "effort_explicit_deep_research_remains_capable",
)

CORE_MESSAGE_QUALITY_SCENARIO_SLUGS = (
    "message_quality_email_meme_trends",
    "message_quality_chat_colorist_sources",
    "message_quality_email_trading_dashboard",
    "message_quality_chat_quonset_leads",
    "message_quality_email_cold_outreach_intro",
    "message_quality_email_cold_outreach_partner",
)

CORE_NATIVE_SCENARIO_SLUGS = (
    GOOGLE_SHEETS_NATIVE_FIND_SHEET_BY_NAME,
    GOOGLE_SHEETS_NATIVE_READ_RANGE,
    GOOGLE_SHEETS_NATIVE_APPEND_ROW,
    GOOGLE_SHEETS_NATIVE_CREATE_AND_FORMAT,
    GOOGLE_SHEETS_NATIVE_MISSING_SELECTED_FILE,
    APOLLO_NATIVE_PEOPLE_SEARCH,
    APOLLO_NATIVE_PERSON_ENRICHMENT,
    APOLLO_NATIVE_MISSING_CONNECTION,
    APOLLO_NATIVE_CREATE_CONTACT,
    HUBSPOT_NATIVE_CONTACT_SEARCH,
    HUBSPOT_NATIVE_DEAL_UPDATE,
    HUBSPOT_NATIVE_MISSING_CONNECTION,
    HUBSPOT_NATIVE_CREATE_CONTACT,
)

CORE_SQLITE_TOOL_RESULT_SCENARIO_SLUGS = (
    SQLITE_MULTI_RESULT_WEB_SYNTHESIS,
    SQLITE_INTERMEDIATE_WORKING_TABLE,
    SQLITE_DEDUPE_REQUERY,
    SQLITE_ITEM_LINK_REPORT,
)

CORE_SCENARIO_SLUGS = tuple(dict.fromkeys([
    "echo_response",
    "weather_lookup",
    *CHARTER_MEMORY_MICRO_SCENARIO_SLUGS,
    *PLANNING_MICRO_SCENARIO_SLUGS,
    *SCHEDULE_INTENT_MICRO_SCENARIO_SLUGS,
    *TOOL_CHOICE_MICRO_SCENARIO_SLUGS[:4],
    *CORE_COMMON_USE_CASE_SCENARIO_SLUGS,
    *CORE_EFFORT_SCENARIO_SLUGS,
    *META_GOBII_REAL_HARNESS_SCENARIO_SLUGS,
    *CORE_NATIVE_SCENARIO_SLUGS,
    *CORE_SQLITE_TOOL_RESULT_SCENARIO_SLUGS,
    *RECRUITMENT_SOURCING_SCENARIO_SLUGS,
    *CORE_MESSAGE_QUALITY_SCENARIO_SLUGS,
    "bitcoin_price_multiturn",
    "linkedin_prefers_brightdata",
    "over_eager_followup",
]))

missing_core_scenarios = [
    slug for slug in CORE_SCENARIO_SLUGS
    if ScenarioRegistry.get(slug) is None
]
if missing_core_scenarios:
    raise ValueError(f"Core eval suite references unregistered scenarios: {missing_core_scenarios}")

# Built-in suites (in addition to the dynamic "all" suite)
register_builtin_suites(
    [
        EvalSuite(
            slug="smoke",
            description="Quick smoke: echo and weather lookups.",
            scenario_slugs=["echo_response", "weather_lookup"],
        ),
        EvalSuite(
            slug="core",
            description="Curated high-signal behavioral regression suite.",
            scenario_slugs=list(CORE_SCENARIO_SLUGS),
        ),
        EvalSuite(
            slug="agent_behavior_micro",
            description="Small deterministic planning and tool-choice behavior checks.",
            scenario_slugs=BEHAVIOR_MICRO_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug="charter_memory_micro",
            description="Small deterministic charter memory behavior checks.",
            scenario_slugs=CHARTER_MEMORY_MICRO_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug="planning_micro",
            description="Small deterministic planning-mode behavior checks.",
            scenario_slugs=PLANNING_MICRO_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug="tool_choice_micro",
            description="Small deterministic obvious tool-choice behavior checks.",
            scenario_slugs=TOOL_CHOICE_MICRO_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug="effort_calibration",
            description="Effort calibration and overwork-prevention behavior checks.",
            scenario_slugs=EFFORT_CALIBRATION_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=META_GOBII_EVAL_SUITE_SLUG,
            description="Meta Gobii system-skill selection, direct-tool planning, and approval-policy evals.",
            scenario_slugs=META_GOBII_EVAL_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=META_GOBII_REAL_HARNESS_SUITE_SLUG,
            description="Real agent-processing Meta Gobii regressions for system-skill discovery and tool use.",
            scenario_slugs=META_GOBII_REAL_HARNESS_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=CUSTOM_TOOL_RESULT_CONTRACT_SUITE_SLUG,
            description="Small custom-tool result contract evals based on real agent trajectory failures.",
            scenario_slugs=CUSTOM_TOOL_RESULT_CONTRACT_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=DAILY_CREDIT_PROMPT_SUITE_SLUG,
            description="Deterministic prompt-policy evals for daily credit limit awareness.",
            scenario_slugs=DAILY_CREDIT_PROMPT_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=SQLITE_TOOL_RESULT_SUITE_SLUG,
            description="SQLite/tool-result synthesis evals for aggregate queries and working tables.",
            scenario_slugs=SQLITE_TOOL_RESULT_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=MESSAGE_QUALITY_SUITE_SLUG,
            description="Message formatting evals for rich reports and restrained simple emails.",
            scenario_slugs=MESSAGE_QUALITY_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=GOOGLE_SHEETS_NATIVE_SUITE_SLUG,
            description="Native Google Sheets system-skill evals over mocked Drive and Sheets REST APIs.",
            scenario_slugs=GOOGLE_SHEETS_NATIVE_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=APOLLO_NATIVE_SUITE_SLUG,
            description="Native Apollo system-skill evals over mocked Apollo REST APIs.",
            scenario_slugs=APOLLO_NATIVE_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=RECRUITMENT_SOURCING_SUITE_SLUG,
            description="Recruitment sourcing system-skill evals over mocked candidate, source, and ledger tools.",
            scenario_slugs=RECRUITMENT_SOURCING_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=HUBSPOT_NATIVE_SUITE_SLUG,
            description="Native HubSpot system-skill evals over mocked HubSpot REST APIs.",
            scenario_slugs=HUBSPOT_NATIVE_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=IMAGE_GENERATION_SUITE_SLUG,
            description="Gobii image-generation skill behaviors over a mocked create_image tool.",
            scenario_slugs=IMAGE_GENERATION_SCENARIO_SLUGS,
        ),
    ]
)
