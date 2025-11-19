import logging
from typing import Dict, Optional
from api.evals.base import EvalScenario

logger = logging.getLogger(__name__)

class ScenarioRegistry:
    _scenarios: Dict[str, EvalScenario] = {}

    @classmethod
    def register(cls, scenario: EvalScenario) -> None:
        if scenario.slug in cls._scenarios:
            logger.warning(f"Overwriting existing scenario with slug: {scenario.slug}")
        cls._scenarios[scenario.slug] = scenario

    @classmethod
    def get(cls, slug: str) -> Optional[EvalScenario]:
        return cls._scenarios.get(slug)

    @classmethod
    def list_all(cls) -> Dict[str, EvalScenario]:
        return cls._scenarios

def register_scenario(cls):
    """Decorator to register a scenario class."""
    instance = cls()
    ScenarioRegistry.register(instance)
    return cls