
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, List, Optional

@dataclass
class ScenarioTask:
    name: str
    assertion_type: str = "manual"  # manual, exact_match, llm_judge, etc.
    description: str = ""
    expected_output: str = ""

@dataclass(frozen=True)
class ScenarioMetadata:
    tags: tuple[str, ...] = field(default_factory=tuple)
    tier: str = "extended"
    category: str = "general"
    expected_runtime: str = "medium"
    cost_class: str = "medium"
    owner: str = "agent-platform"
    area: str = "agent_behavior"
    supports_simulation: bool = False
    required_fixtures: tuple[str, ...] = field(default_factory=tuple)
    required_secrets: tuple[str, ...] = field(default_factory=tuple)


def _normalized_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Iterable[Any] = (value,)
    else:
        values = value
    return tuple(
        str(item).strip()
        for item in values
        if str(item).strip()
    )


class EvalScenario:
    """
    Base class for evaluation scenarios.
    Subclasses must define `slug`, `description`, and implement `run()`.
    """
    slug: str
    version: str = "1.0.0"
    description: str = ""
    tasks: List[ScenarioTask] = []
    metadata: Optional[ScenarioMetadata] = None

    tags: tuple[str, ...] = ()
    tier: str = "extended"
    category: str = "general"
    expected_runtime: str = "medium"
    cost_class: str = "medium"
    owner: str = "agent-platform"
    area: str = "agent_behavior"
    supports_simulation: bool = False
    required_fixtures: tuple[str, ...] = ()
    required_secrets: tuple[str, ...] = ()
    requires_personal_agent: bool = False

    def get_metadata(self) -> ScenarioMetadata:
        metadata = self.metadata or ScenarioMetadata(
            tags=_normalized_tuple(getattr(self, "tags", ())),
            tier=str(getattr(self, "tier", "") or "extended"),
            category=str(getattr(self, "category", "") or "general"),
            expected_runtime=str(getattr(self, "expected_runtime", "") or "medium"),
            cost_class=str(getattr(self, "cost_class", "") or "medium"),
            owner=str(getattr(self, "owner", "") or "agent-platform"),
            area=str(getattr(self, "area", "") or "agent_behavior"),
            supports_simulation=bool(getattr(self, "supports_simulation", False)),
            required_fixtures=_normalized_tuple(getattr(self, "required_fixtures", ())),
            required_secrets=_normalized_tuple(getattr(self, "required_secrets", ())),
        )

        return replace(
            metadata,
            tags=_normalized_tuple(metadata.tags),
            tier=str(metadata.tier or "extended"),
            category=str(metadata.category or "general"),
            expected_runtime=str(metadata.expected_runtime or "medium"),
            cost_class=str(metadata.cost_class or "medium"),
            owner=str(metadata.owner or "agent-platform"),
            area=str(metadata.area or "agent_behavior"),
            supports_simulation=bool(
                metadata.supports_simulation or getattr(self, "supports_simulation", False)
            ),
            required_fixtures=_normalized_tuple(metadata.required_fixtures),
            required_secrets=_normalized_tuple(metadata.required_secrets),
        )

    def run(self, run_id: str, agent_id: str) -> None:
        """
        Execute the scenario.
        
        Args:
            run_id: The ID of the EvalRun.
            agent_id: The ID of the PersistentAgent being tested.
        """
        raise NotImplementedError("Scenarios must implement the run() method.")
