
from api.evals.registry import ScenarioRegistry
# Import scenarios here to ensure they are registered when the registry is imported elsewhere
from api.evals.scenarios import * # noqa
from api.evals.scenarios.behavior_micro import (
    BEHAVIOR_MICRO_SCENARIO_SLUGS,
    CHARTER_MEMORY_MICRO_SCENARIO_SLUGS,
    PLANNING_MICRO_SCENARIO_SLUGS,
    TOOL_CHOICE_MICRO_SCENARIO_SLUGS,
)
from api.evals.scenarios.effort_calibration import EFFORT_CALIBRATION_SCENARIO_SLUGS
from api.evals.scenarios.custom_tool_result_contract import (
    CUSTOM_TOOL_RESULT_CONTRACT_SCENARIO_SLUGS,
    CUSTOM_TOOL_RESULT_CONTRACT_SUITE_SLUG,
)
from api.evals.scenarios.sqlite_tool_results import (
    SQLITE_TOOL_RESULT_SCENARIO_SLUGS,
    SQLITE_TOOL_RESULT_SUITE_SLUG,
)
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
            slug=CUSTOM_TOOL_RESULT_CONTRACT_SUITE_SLUG,
            description="Small custom-tool result contract evals based on real agent trajectory failures.",
            scenario_slugs=CUSTOM_TOOL_RESULT_CONTRACT_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug=SQLITE_TOOL_RESULT_SUITE_SLUG,
            description="SQLite/tool-result synthesis evals for aggregate queries and working tables.",
            scenario_slugs=SQLITE_TOOL_RESULT_SCENARIO_SLUGS,
        ),
    ]
)
