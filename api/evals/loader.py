
from api.evals.registry import ScenarioRegistry, register_scenario
# Import scenarios here to ensure they are registered when the registry is imported elsewhere
from api.evals.scenarios import * # noqa
from api.evals.scenarios.behavior_micro import (
    BEHAVIOR_MICRO_SCENARIO_SLUGS,
    PLANNING_MICRO_SCENARIO_SLUGS,
    TOOL_CHOICE_MICRO_SCENARIO_SLUGS,
)
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
            slug="planning_micro",
            description="Small deterministic planning-mode behavior checks.",
            scenario_slugs=PLANNING_MICRO_SCENARIO_SLUGS,
        ),
        EvalSuite(
            slug="tool_choice_micro",
            description="Small deterministic obvious tool-choice behavior checks.",
            scenario_slugs=TOOL_CHOICE_MICRO_SCENARIO_SLUGS,
        ),
    ]
)
