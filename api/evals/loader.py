
from api.evals.registry import ScenarioRegistry, register_scenario
# Import scenarios here to ensure they are registered when the registry is imported elsewhere
from api.evals.scenarios import * # noqa
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
    ]
)
