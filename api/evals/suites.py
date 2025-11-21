from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from api.evals.registry import ScenarioRegistry


@dataclass(frozen=True)
class EvalSuite:
    slug: str
    description: str
    scenario_slugs: List[str]


class SuiteRegistry:
    """Registry of eval suites (collections of scenarios)."""

    _suites: Dict[str, EvalSuite] = {}

    @classmethod
    def register(cls, suite: EvalSuite) -> None:
        cls._suites[suite.slug] = suite

    @classmethod
    def get(cls, slug: str) -> Optional[EvalSuite]:
        if slug == "all":
            return cls._build_all_suite()
        return cls._suites.get(slug)

    @classmethod
    def list_all(cls) -> Dict[str, EvalSuite]:
        suites = dict(cls._suites)
        suites["all"] = cls._build_all_suite()
        return suites

    @classmethod
    def _build_all_suite(cls) -> EvalSuite:
        scenarios = list(ScenarioRegistry.list_all().values())
        scenario_slugs = [scenario.slug for scenario in scenarios]
        return EvalSuite(
            slug="all",
            description="Run every registered scenario concurrently.",
            scenario_slugs=scenario_slugs,
        )


def register_builtin_suites(suites: Iterable[EvalSuite]) -> None:
    for suite in suites:
        SuiteRegistry.register(suite)

