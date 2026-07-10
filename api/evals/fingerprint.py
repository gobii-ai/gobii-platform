"""
Fingerprinting utilities for eval scenarios.

Provides mechanisms to uniquely identify eval code and execution context
for comparison and reproducibility tracking.
"""

import ast
from dataclasses import fields, is_dataclass
from functools import lru_cache
import hashlib
import inspect
import json
import subprocess
import textwrap
from pathlib import Path
from typing import Any


def _stable_value(value: Any) -> Any:
    """Return a deterministic JSON-compatible representation of eval data."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _stable_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _stable_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_stable_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return {"type": f"{value.__class__.__module__}.{value.__class__.__qualname__}"}


@lru_cache(maxsize=None)
def _normalized_source(obj: Any) -> str:
    """Normalize one explicitly relevant class or helper without hashing its whole module."""
    try:
        source = textwrap.dedent(inspect.getsource(obj))
        return ast.dump(ast.parse(source), annotate_fields=False)
    except (OSError, TypeError, SyntaxError):
        module = getattr(obj, "__module__", obj.__class__.__module__)
        qualname = getattr(obj, "__qualname__", obj.__class__.__qualname__)
        return f"{module}.{qualname}"


def _source_identity(obj: Any) -> str:
    module = getattr(obj, "__module__", obj.__class__.__module__)
    qualname = getattr(obj, "__qualname__", obj.__class__.__qualname__)
    return f"{module}.{qualname}"


def _fingerprint_dependencies(cls: type) -> list[Any]:
    dependencies = []
    seen = set()
    for base in reversed(cls.__mro__):
        for dependency in base.__dict__.get("fingerprint_dependencies", ()):
            identity = _source_identity(dependency)
            if identity in seen:
                continue
            seen.add(identity)
            dependencies.append(dependency)
    return dependencies


def _scenario_definition(scenario: Any, cls: type) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "class": f"{cls.__module__}.{cls.__qualname__}",
    }
    for name in (
        "slug",
        "version",
        "description",
        "tasks",
        "metadata",
        "tags",
        "tier",
        "category",
        "expected_runtime",
        "cost_class",
        "owner",
        "area",
        "supports_simulation",
        "required_fixtures",
        "required_secrets",
        "requires_personal_agent",
        "system_skill_key",
        "system_skill_name",
        "native_provider_key",
        "forbidden_tool_names",
        "forbidden_tool_prefixes",
        "allowed_tool_names",
        "max_relevant_tool_calls",
        "fingerprint_data",
        "case",
    ):
        if hasattr(scenario, name):
            definition[name] = _stable_value(getattr(scenario, name))
    return definition


def compute_scenario_fingerprint(scenario) -> str:
    """
    Compute a fingerprint for a scenario definition and its shared behavior.

    This captures the behavioral identity of the scenario - if the code
    changes in any meaningful way, the fingerprint changes.

    Uses AST (Abstract Syntax Tree) normalization to ignore:
    - Whitespace differences
    - Comment changes
    - Formatting variations

    Args:
        scenario: An EvalScenario instance or class

    Returns:
        16-character hex string fingerprint
    """
    cls = scenario if isinstance(scenario, type) else scenario.__class__
    scenario_definition = scenario if not isinstance(scenario, type) else cls
    behavior_classes = [
        base
        for base in cls.__mro__
        if base.__module__.startswith("api.evals")
    ]
    dependencies = _fingerprint_dependencies(cls)
    payload = {
        "definition": _scenario_definition(scenario_definition, cls),
        "behavior_classes": {
            _source_identity(base): _normalized_source(base)
            for base in behavior_classes
        },
        "shared_dependencies": {
            _source_identity(dependency): _normalized_source(dependency)
            for dependency in dependencies
        },
    }
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def get_code_version() -> str:
    """
    Get the current git commit hash.

    Tries git first (for local development), then falls back to .git-commit
    file (for Docker deployments where git isn't available).

    Returns:
        12-character short git hash, or empty string if unavailable
    """
    # Try git first (local dev)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=_get_repo_root(),
        )
        if result.returncode == 0:
            return result.stdout.strip()[:12]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Fall back to .git-commit file (Docker deployments)
    # The file is written during Docker build with the commit SHA
    commit_file = Path(__file__).parent.parent.parent / ".git-commit"
    if commit_file.exists():
        content = commit_file.read_text().strip()
        if content and content != "unknown":
            return content[:12]

    return ""


def get_code_branch() -> str:
    """
    Get the current git branch name.

    Returns:
        Branch name, or empty string if not in a git repo or detached HEAD
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=_get_repo_root(),
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            # "HEAD" is returned for detached HEAD state
            return "" if branch == "HEAD" else branch
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return ""


def _get_repo_root() -> str:
    """Get the git repository root directory."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def get_primary_model(routing_profile) -> str:
    """
    Extract the primary model name from an LLM routing profile.

    Traverses the profile structure to find the first/primary model:
    Profile → TokenRange (lowest min) → Tier (order=1) → Endpoint → litellm_model

    Args:
        routing_profile: An LLMRoutingProfile instance

    Returns:
        Model name string (e.g., 'claude-sonnet-4'), or empty string if not found
    """
    if not routing_profile:
        return ""

    try:
        # Get the first token range (lowest min_tokens)
        token_range = (
            routing_profile.persistent_token_ranges
            .order_by("min_tokens")
            .first()
        )
        if not token_range:
            return ""

        # Get the first tier (order=1, standard intelligence tier for routing)
        tier = (
            token_range.tiers
            .filter(intelligence_tier__key="standard")
            .order_by("order")
            .first()
        )
        if not tier:
            # Fall back to any tier
            tier = token_range.tiers.order_by("order").first()
        if not tier:
            return ""

        # Get the highest-weighted endpoint in this tier
        tier_endpoint = (
            tier.tier_endpoints
            .select_related("endpoint")
            .order_by("-weight")
            .first()
        )
        if not tier_endpoint or not tier_endpoint.endpoint:
            return ""

        return tier_endpoint.endpoint.litellm_model or ""

    except Exception:
        # Don't fail the eval run if we can't extract the model
        return ""
