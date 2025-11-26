"""
Fingerprinting utilities for eval scenarios.

Provides mechanisms to uniquely identify eval code and execution context
for comparison and reproducibility tracking.
"""

import ast
import hashlib
import inspect
import subprocess
import textwrap


def compute_scenario_fingerprint(scenario) -> str:
    """
    Compute a fingerprint for a scenario class using AST hashing.

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
    # Get the class if we were passed an instance
    cls = scenario if isinstance(scenario, type) else scenario.__class__

    try:
        source = inspect.getsource(cls)
        # Dedent to handle classes defined inside functions/methods
        source = textwrap.dedent(source)
        tree = ast.parse(source)
        # ast.dump with no annotations gives a normalized representation
        normalized = ast.dump(tree, annotate_fields=False)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]
    except (OSError, TypeError, SyntaxError):
        # Fallback if source unavailable (e.g., dynamically generated)
        # Use class name + module as degraded fingerprint
        fallback = f"{cls.__module__}.{cls.__name__}"
        return hashlib.sha256(fallback.encode()).hexdigest()[:16]


def get_code_version() -> str:
    """
    Get the current git commit hash.

    Returns:
        12-character short git hash, or empty string if not in a git repo
    """
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

        # Get the first tier (order=1, non-premium for standard routing)
        tier = (
            token_range.tiers
            .filter(is_premium=False, is_max=False)
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
