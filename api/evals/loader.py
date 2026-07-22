
from api.evals.registry import ScenarioRegistry
# Import scenarios here to ensure they are registered when the registry is imported elsewhere
from api.evals.scenarios import * # noqa
from api.evals.scenarios.behavior_micro import BEHAVIOR_MICRO_SCENARIO_SLUGS, CHARTER_MEMORY_MICRO_SCENARIO_SLUGS, PLANNING_MICRO_SCENARIO_SLUGS, TOOL_CHOICE_MICRO_SCENARIO_SLUGS
from api.evals.scenarios.effort_calibration import EFFORT_CALIBRATION_SCENARIO_SLUGS
from api.evals.scenarios.custom_tool_result_contract import CUSTOM_TOOL_RESULT_CONTRACT_SCENARIO_SLUGS, CUSTOM_TOOL_RESULT_CONTRACT_SUITE_SLUG
from api.evals.scenarios.daily_credit_prompt import DAILY_CREDIT_PROMPT_SCENARIO_SLUGS, DAILY_CREDIT_PROMPT_SUITE_SLUG
from api.evals.scenarios.sqlite_tool_results import SQLITE_TOOL_RESULT_SCENARIO_SLUGS, SQLITE_TOOL_RESULT_SUITE_SLUG
from api.evals.scenarios.message_quality import MESSAGE_QUALITY_SCENARIO_SLUGS, MESSAGE_QUALITY_SUITE_SLUG
from api.evals.scenarios.google_sheets_native import GOOGLE_SHEETS_NATIVE_SCENARIO_SLUGS, GOOGLE_SHEETS_NATIVE_SUITE_SLUG
from api.evals.scenarios.apollo_native import APOLLO_NATIVE_SCENARIO_SLUGS, APOLLO_NATIVE_SUITE_SLUG
from api.evals.scenarios.recruitment_sourcing import RECRUITMENT_SOURCING_SCENARIO_SLUGS, RECRUITMENT_SOURCING_SUITE_SLUG
from api.evals.scenarios.hubspot_native import HUBSPOT_NATIVE_SCENARIO_SLUGS, HUBSPOT_NATIVE_SUITE_SLUG
from api.evals.scenarios.discord_native import DISCORD_NATIVE_SCENARIO_SLUGS, DISCORD_NATIVE_SUITE_SLUG
from api.evals.scenarios.image_generation import IMAGE_GENERATION_SCENARIO_SLUGS, IMAGE_GENERATION_SUITE_SLUG
from api.evals.scenarios.responsibility_boundaries import RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS, RESPONSIBILITY_BOUNDARY_SUITE_SLUG
from api.evals.scenarios.hallucinated_links import HALLUCINATED_LINK_SCENARIO_SLUGS, HALLUCINATED_LINKS_SUITE_SLUG
from api.evals.scenarios.agent_scheduling import AGENT_SCHEDULING_SCENARIO_SLUGS, AGENT_SCHEDULING_SUITE_SLUG
from api.evals.scenarios.meta_gobii import META_GOBII_REAL_HARNESS_SCENARIO_SLUGS, META_GOBII_REAL_HARNESS_SUITE_SLUG
from api.evals.meta_gobii import META_GOBII_EVAL_SCENARIO_SLUGS, META_GOBII_EVAL_SUITE_SLUG
from api.evals.suites import EvalSuite, register_builtin_suites

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
            description="Core regression: all registered scenarios.",
            scenario_slugs=[scenario.slug for scenario in ScenarioRegistry.list_all().values()],
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
            slug=DISCORD_NATIVE_SUITE_SLUG,
            description="Native Discord reply-context and reaction behavior over the real agent harness.",
            scenario_slugs=DISCORD_NATIVE_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=IMAGE_GENERATION_SUITE_SLUG,
            description="Gobii image-generation skill behaviors over a mocked create_image tool.",
            scenario_slugs=IMAGE_GENERATION_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=RESPONSIBILITY_BOUNDARY_SUITE_SLUG,
            description="Connected-agent ownership, handoff, and shared-channel responsibility regressions.",
            scenario_slugs=RESPONSIBILITY_BOUNDARY_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=HALLUCINATED_LINKS_SUITE_SLUG,
            description="Link-grounding evals for URL association and construction failures across short and long contexts.",
            scenario_slugs=HALLUCINATED_LINK_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=AGENT_SCHEDULING_SUITE_SLUG,
            description="Multiple schedules, precise timers, targeted changes, and bounded scheduling guardrails.",
            scenario_slugs=AGENT_SCHEDULING_SCENARIO_SLUGS,
        ),
    ]
)
