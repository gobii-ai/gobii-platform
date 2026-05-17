from dataclasses import dataclass, field
from typing import Iterable

from api.evals.base import EvalScenario, ScenarioMetadata
from api.evals.registry import ScenarioRegistry
from api.evals.suites import SuiteRegistry


@dataclass(frozen=True)
class ScenarioCatalogFilters:
    tags: tuple[str, ...] = field(default_factory=tuple)
    tiers: tuple[str, ...] = field(default_factory=tuple)
    categories: tuple[str, ...] = field(default_factory=tuple)
    cost_classes: tuple[str, ...] = field(default_factory=tuple)
    runtime_classes: tuple[str, ...] = field(default_factory=tuple)
    owners: tuple[str, ...] = field(default_factory=tuple)
    areas: tuple[str, ...] = field(default_factory=tuple)
    required_secrets: tuple[str, ...] = field(default_factory=tuple)
    simulation_supported: bool | None = None


def normalized_filter_values(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    normalized: list[str] = []
    for value in values:
        for part in str(value).split(","):
            part = part.strip().lower()
            if part:
                normalized.append(part)
    return tuple(dict.fromkeys(normalized))


def get_scenario_metadata(scenario: EvalScenario) -> ScenarioMetadata:
    return scenario.get_metadata()


def scenario_to_suite_slugs() -> dict[str, list[str]]:
    suites = SuiteRegistry.list_all()
    mapping: dict[str, list[str]] = {}
    for suite_slug, suite in suites.items():
        if suite_slug == "all":
            continue
        for scenario_slug in suite.scenario_slugs:
            mapping.setdefault(scenario_slug, []).append(suite_slug)
    for suite_slugs in mapping.values():
        suite_slugs.sort()
    return mapping


def scenario_matches_filters(
    scenario: EvalScenario,
    filters: ScenarioCatalogFilters,
) -> bool:
    metadata = get_scenario_metadata(scenario)
    scenario_tags = {tag.lower() for tag in metadata.tags}
    required_secrets = {secret.lower() for secret in metadata.required_secrets}

    if filters.tags and not scenario_tags.intersection(filters.tags):
        return False
    if filters.tiers and metadata.tier.lower() not in filters.tiers:
        return False
    if filters.categories and metadata.category.lower() not in filters.categories:
        return False
    if filters.cost_classes and metadata.cost_class.lower() not in filters.cost_classes:
        return False
    if filters.runtime_classes and metadata.expected_runtime.lower() not in filters.runtime_classes:
        return False
    if filters.owners and metadata.owner.lower() not in filters.owners:
        return False
    if filters.areas and metadata.area.lower() not in filters.areas:
        return False
    if filters.required_secrets and not required_secrets.intersection(filters.required_secrets):
        return False
    if filters.simulation_supported is not None and metadata.supports_simulation != filters.simulation_supported:
        return False
    return True


def filter_scenario_slugs(
    scenario_slugs: Iterable[str],
    filters: ScenarioCatalogFilters,
) -> list[str]:
    selected = []
    for scenario_slug in dict.fromkeys(scenario_slugs):
        scenario = ScenarioRegistry.get(scenario_slug)
        if scenario and scenario_matches_filters(scenario, filters):
            selected.append(scenario_slug)
    return selected


def serialize_scenario_catalog_item(
    scenario: EvalScenario,
    *,
    suite_slugs: Iterable[str] = (),
) -> dict:
    metadata = get_scenario_metadata(scenario)
    return {
        "slug": scenario.slug,
        "version": getattr(scenario, "version", "") or "",
        "description": scenario.description,
        "suite_slugs": list(suite_slugs),
        "task_count": len(getattr(scenario, "tasks", []) or []),
        "metadata": {
            "tags": list(metadata.tags),
            "tier": metadata.tier,
            "category": metadata.category,
            "expected_runtime": metadata.expected_runtime,
            "cost_class": metadata.cost_class,
            "owner": metadata.owner,
            "area": metadata.area,
            "supports_simulation": metadata.supports_simulation,
            "required_fixtures": list(metadata.required_fixtures),
            "required_secrets": list(metadata.required_secrets),
        },
    }
