#!/usr/bin/env python3
"""Check committed source LoC and rendered prompt-size budgets."""

import argparse
import json
import logging
import os
import subprocess
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
BUDGET_PATH = REPO_ROOT / "scripts" / "budgets" / "complexity_budgets.json"
FIXED_PROMPT_NOW = datetime(2026, 5, 20, 18, 0, 0, tzinfo=timezone.utc)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SOURCE_ROOTS = (
    "agents/",
    "api/",
    "assets/",
    "billing/",
    "config/",
    "console/",
    "constants/",
    "docker/",
    "frontend/src/",
    "middleware/",
    "sandbox_server/server/",
    "tasks/",
    "templatetags/",
    "util/",
)
SOURCE_FILES = {
    "manage.py",
    "pyproject.toml",
    "sandbox_server/pyproject.toml",
}
SOURCE_SUFFIXES = {
    ".bash",
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".py",
    ".pyi",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
    ".zsh",
}
EXCLUDED_PREFIXES = (
    ".factory/",
    ".github/",
    ".local/",
    "api/migrations/",
    "console/migrations/",
    "docs/",
    "frontend/node_modules/",
    "frontend/src/components/pets/",
    "gobii_platform.egg-info/",
    "marketing_events/",
    "mediafiles/",
    "misc/",
    "pages/",
    "pages/migrations/",
    "proprietary/",
    "scripts/",
    "setup/",
    "static/css/",
    "static/frontend/",
    "static/js/",
    "templates/",
    "vendor/",
)
EXCLUDED_PARTS = (
    "/__pycache__/",
    "/build/",
    "/cache/",
    "/dist/",
    "/migrations/",
    "/node_modules/",
)
EXCLUDED_FILENAMES = {
    "api/agent/system_skills/defaults.py",
    "api/agent/system_skills/native_api_cookbooks.py",
    "api/services/user_pets.py",
    "console/user_pets_api.py",
    "frontend/src/api/userPets.ts",
    "frontend/src/hooks/useUserPets.ts",
    "package-lock.json",
    "uv.lock",
}
TEST_EXCLUDED_PREFIXES = (
    "frontend/src/test/",
    "tests/",
)
TEST_EXCLUDED_PARTS = (
    "/__tests__/",
    "/tests/",
)
TEST_EXCLUDED_FILES = {
    "config/minimal_test_settings.py",
    "config/test_settings.py",
}
TEST_FILE_SUFFIXES = {
    ".js",
    ".jsx",
    ".py",
    ".ts",
    ".tsx",
}
EVAL_EXCLUDED_PREFIXES = (
    "api/evals/",
    "api/templates/evals/",
    "console/evals/",
    "frontend/src/components/evals/",
)
EVAL_EXCLUDED_FILES = {
    "api/agent/eval_agents.py",
    "api/agent/tools/eval_synthetic_tools.py",
    "api/management/commands/run_evals.py",
    "config/eval_local_settings.py",
    "config/eval_postgres_settings.py",
    "console/templates/console/evals.html",
    "console/templates/console/evals_detail.html",
    "frontend/src/api/evals.ts",
    "frontend/src/screens/EvalsDetailScreen.tsx",
    "frontend/src/screens/EvalsScreen.tsx",
}
SOURCE_LOC_EXCLUDE_START = "complexity-budget: exclude-start"
SOURCE_LOC_EXCLUDE_END = "complexity-budget: exclude-end"


@dataclass(frozen=True)
class SourceLocMeasurement:
    line_count: int
    file_count: int


class BudgetFailure(RuntimeError):
    """Raised when a measured budget exceeds the committed limit."""


def _run_git(args: list[str]) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
    )


def _git_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
    )
    return [
        item.decode("utf-8")
        for item in output.split(b"\0")
        if item
    ]


def _is_test_source(path: str) -> bool:
    if path in TEST_EXCLUDED_FILES:
        return True
    if any(path.startswith(prefix) for prefix in TEST_EXCLUDED_PREFIXES):
        return True
    normalized = f"/{path}"
    if any(part in normalized for part in TEST_EXCLUDED_PARTS):
        return True

    path_obj = Path(path)
    if path_obj.suffix not in TEST_FILE_SUFFIXES:
        return False

    name = path_obj.name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or any(
            name.endswith(f".{kind}{path_obj.suffix}")
            for kind in ("test", "spec")
        )
    )


def _is_eval_source(path: str) -> bool:
    return path in EVAL_EXCLUDED_FILES or any(
        path.startswith(prefix)
        for prefix in EVAL_EXCLUDED_PREFIXES
    )


def _is_counted_source(path: str) -> bool:
    if path in EXCLUDED_FILENAMES:
        return False
    if any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False
    normalized = f"/{path}"
    if any(part in normalized for part in EXCLUDED_PARTS):
        return False
    if _is_test_source(path) or _is_eval_source(path):
        return False
    if path in SOURCE_FILES:
        return True
    if not any(path.startswith(root) for root in SOURCE_ROOTS):
        return False
    return Path(path).suffix in SOURCE_SUFFIXES


def _source_loc_exclusion_name(line: str, marker: str) -> str:
    suffix = line.split(marker, 1)[1].strip()
    return suffix.split(maxsplit=1)[0] if suffix else ""


def _count_nonblank_lines(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    line_count = 0
    excluded_region: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if SOURCE_LOC_EXCLUDE_START in stripped:
            if excluded_region is not None:
                raise BudgetFailure(f"Nested source LoC exclusion in {path}")
            excluded_region = _source_loc_exclusion_name(stripped, SOURCE_LOC_EXCLUDE_START)
            if not excluded_region:
                raise BudgetFailure(f"Unnamed source LoC exclusion in {path}")
            continue
        if SOURCE_LOC_EXCLUDE_END in stripped:
            region = _source_loc_exclusion_name(stripped, SOURCE_LOC_EXCLUDE_END)
            if excluded_region != region:
                raise BudgetFailure(f"Mismatched source LoC exclusion in {path}")
            excluded_region = None
            continue
        if excluded_region is None and stripped:
            line_count += 1
    if excluded_region is not None:
        raise BudgetFailure(f"Unclosed source LoC exclusion in {path}")
    return line_count


def measure_source_loc() -> SourceLocMeasurement:
    line_count = 0
    file_count = 0
    for relative_path in sorted(_git_files()):
        if not _is_counted_source(relative_path):
            continue
        path = REPO_ROOT / relative_path
        if not path.exists():
            continue
        line_count += _count_nonblank_lines(path)
        file_count += 1
    return SourceLocMeasurement(line_count=line_count, file_count=file_count)


def _json_size(value: Any) -> int:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return len(payload.encode("utf-8"))


def _text_size(value: str) -> int:
    return len(value.encode("utf-8"))


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz: timezone | None = None) -> datetime:
        if tz is None:
            return FIXED_PROMPT_NOW.replace(tzinfo=None)
        return FIXED_PROMPT_NOW.astimezone(tz)


def _setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")
    import django

    django.setup()


def _daily_credit_state() -> dict[str, Any]:
    return {
        "soft_target": Decimal("20"),
        "soft_target_remaining": Decimal("19"),
        "soft_target_exceeded": False,
        "hard_limit": Decimal("40"),
        "hard_limit_remaining": Decimal("39"),
        "used": Decimal("1"),
        "next_reset": FIXED_PROMPT_NOW + timedelta(days=1),
        "burn_rate_per_hour": Decimal("0"),
        "burn_rate_window_minutes": 60,
        "burn_rate_threshold_per_hour": Decimal("20"),
    }


def _create_agent(*, scenario: str, planning: bool = False):
    from allauth.account.models import EmailAddress
    from django.contrib.auth import get_user_model

    from api.models import BrowserUseAgent, CommsChannel, PersistentAgent, PersistentAgentCommsEndpoint

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=f"{scenario}@example.com",
        email=f"{scenario}@example.com",
        password="unused-test-password",
    )
    EmailAddress.objects.create(
        user=user,
        email=user.email,
        verified=True,
        primary=True,
    )
    browser_agent = BrowserUseAgent.objects.create(
        user=user,
        name=f"{scenario} Browser Agent",
    )
    planning_state = (
        PersistentAgent.PlanningState.PLANNING
        if planning
        else PersistentAgent.PlanningState.COMPLETED
    )
    agent = PersistentAgent.objects.create(
        user=user,
        name=f"{scenario} Agent",
        charter="Track exact-source updates and report concise findings.",
        browser_use_agent=browser_agent,
        planning_state=planning_state,
    )
    endpoint = PersistentAgentCommsEndpoint.objects.create(
        owner_agent=agent,
        channel=CommsChannel.EMAIL,
        address=f"{scenario}-agent@example.com",
        is_primary=True,
    )
    external_endpoint = PersistentAgentCommsEndpoint.objects.create(
        channel=CommsChannel.EMAIL,
        address=f"{scenario}-recipient@example.com",
    )
    agent.preferred_contact_endpoint = external_endpoint
    agent.save(update_fields=["preferred_contact_endpoint", "updated_at"])
    PersistentAgent.objects.filter(pk=agent.pk).update(
        created_at=FIXED_PROMPT_NOW - timedelta(hours=1),
        last_interaction_at=FIXED_PROMPT_NOW - timedelta(hours=1),
    )
    agent.refresh_from_db()
    return agent, user, endpoint


def _measure_prompt_scenario(
    *,
    name: str,
    is_first_run: bool = False,
    planning: bool = False,
    web_session: bool = False,
) -> dict[str, int]:
    from api.agent.core.prompt_context import build_prompt_context_preview, get_agent_tools
    from api.services.web_sessions import start_web_session

    agent, user, _endpoint = _create_agent(scenario=name, planning=planning)
    if web_session:
        start_web_session(agent, user)

    messages, fitted_tokens, _metadata = build_prompt_context_preview(
        agent,
        is_first_run=is_first_run,
        daily_credit_state=_daily_credit_state(),
        prefer_low_latency=web_session,
    )
    system_message = next(message["content"] for message in messages if message["role"] == "system")
    user_message = next(message["content"] for message in messages if message["role"] == "user")
    tools = get_agent_tools(agent)

    system_bytes = _text_size(system_message)
    user_bytes = _text_size(user_message)
    tools_bytes = _json_size(tools)
    return {
        "system_bytes": system_bytes,
        "user_bytes": user_bytes,
        "tools_bytes": tools_bytes,
        "total_bytes": system_bytes + user_bytes + tools_bytes,
        "fitted_tokens": int(fitted_tokens),
    }


def measure_prompt_sizes() -> dict[str, dict[str, int]]:
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.WARNING)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            warnings.simplefilter("ignore", UserWarning)
            _setup_django()
            from django.test.utils import setup_databases, teardown_databases
            from billing.addons import AddonUplift

            test_config = setup_databases(verbosity=0, interactive=False)
            try:
                zero_addons = AddonUplift()
                patches = (
                    patch(
                        "api.agent.core.prompt_context.get_llm_config_with_failover",
                        return_value=[("endpoint", "openai/gpt-4o-mini", {})],
                    ),
                    patch(
                        "api.agent.core.prompt_context.get_owner_plan",
                        return_value={"id": "startup", "name": "Pro", "max_contacts_per_agent": 20},
                    ),
                    patch("api.agent.core.prompt_context.AddonEntitlementService.get_uplift", return_value=zero_addons),
                    patch("billing.addons.AddonEntitlementService.get_uplift", return_value=zero_addons),
                    patch("api.agent.core.prompt_context.DedicatedProxyService.allocated_count", return_value=0),
                    patch("api.agent.core.prompt_context.sandbox_compute_enabled_for_agent", return_value=True),
                    patch("api.agent.tools.tool_manager.sandbox_compute_enabled_for_agent", return_value=True),
                    patch("api.agent.core.prompt_context.datetime", FixedDateTime),
                    patch("api.agent.core.prompt_context.dj_timezone.now", return_value=FIXED_PROMPT_NOW),
                    patch("api.models.PersistentAgent._sync_celery_beat_task", return_value=None),
                )
                with (
                    patches[0],
                    patches[1],
                    patches[2],
                    patches[3],
                    patches[4],
                    patches[5],
                    patches[6],
                    patches[7],
                    patches[8],
                    patches[9],
                ):
                    return {
                        "normal_explicit_send": _measure_prompt_scenario(name="normal-explicit-send"),
                        "web_chat_implied_send": _measure_prompt_scenario(
                            name="web-chat-implied-send",
                            web_session=True,
                        ),
                        "planning_first_run": _measure_prompt_scenario(
                            name="planning-first-run",
                            is_first_run=True,
                            planning=True,
                        ),
                    }
            finally:
                teardown_databases(test_config, verbosity=0)
    finally:
        logging.disable(previous_logging_disable)


def _load_budget() -> dict[str, Any]:
    if not BUDGET_PATH.exists():
        raise BudgetFailure(
            f"Budget file is missing: {BUDGET_PATH.relative_to(REPO_ROOT)}. "
            "Run with --update-baselines after the baseline commit is approved."
        )
    return json.loads(BUDGET_PATH.read_text(encoding="utf-8"))


def _write_budget(budget: dict[str, Any]) -> None:
    BUDGET_PATH.write_text(
        json.dumps(budget, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _budget_metadata(baseline_sha: str) -> dict[str, Any]:
    return {
        "baseline_sha": baseline_sha,
        "generated_by": "uv run python scripts/check_complexity_budgets.py --update-baselines",
        "source_loc": {
            "description": "Nonblank lines in core product source files, excluding tests, evals, and named feature regions.",
            "include_roots": list(SOURCE_ROOTS),
            "include_files": sorted(SOURCE_FILES),
            "include_suffixes": sorted(SOURCE_SUFFIXES),
            "exclude_prefixes": list(EXCLUDED_PREFIXES),
            "exclude_parts": list(EXCLUDED_PARTS),
            "exclude_filenames": sorted(EXCLUDED_FILENAMES),
            "exclude_test_prefixes": list(TEST_EXCLUDED_PREFIXES),
            "exclude_test_parts": list(TEST_EXCLUDED_PARTS),
            "exclude_test_files": sorted(TEST_EXCLUDED_FILES),
            "exclude_test_file_suffixes": sorted(TEST_FILE_SUFFIXES),
            "exclude_eval_prefixes": list(EVAL_EXCLUDED_PREFIXES),
            "exclude_eval_files": sorted(EVAL_EXCLUDED_FILES),
            "exclude_region_markers": {
                "start": f"{SOURCE_LOC_EXCLUDE_START} <name>",
                "end": f"{SOURCE_LOC_EXCLUDE_END} <name>",
            },
        },
        "prompt_size": {
            "description": "Rendered prompt messages plus JSON tool definitions for representative send/planning paths.",
            "unit": "utf8_bytes",
            "scenarios": {
                "normal_explicit_send": "No active web session; communication requires explicit send tools.",
                "web_chat_implied_send": "Active web-chat session; text replies can use implied send.",
                "planning_first_run": "Planning mode on the first run with a verified preferred contact.",
            },
        },
    }


def update_baselines(*, baseline_sha: str, loc_only: bool, prompt_only: bool) -> dict[str, Any]:
    budget = _budget_metadata(baseline_sha)
    existing = _load_budget() if BUDGET_PATH.exists() else {}

    if not prompt_only:
        loc = measure_source_loc()
        budget["source_loc"]["limit"] = loc.line_count
        budget["source_loc"]["file_count"] = loc.file_count
    elif "source_loc" in existing:
        budget["source_loc"] = existing["source_loc"]

    if not loc_only:
        prompt_sizes = measure_prompt_sizes()
        budget["prompt_size"]["limits"] = prompt_sizes
    elif "prompt_size" in existing:
        budget["prompt_size"] = existing["prompt_size"]

    _write_budget(budget)
    return budget


def check_source_loc(budget: dict[str, Any]) -> SourceLocMeasurement:
    measurement = measure_source_loc()
    limit = int(budget["source_loc"]["limit"])
    if measurement.line_count > limit:
        delta = measurement.line_count - limit
        raise BudgetFailure(
            "Source LoC budget exceeded: "
            f"current={measurement.line_count}, limit={limit}, delta=+{delta}. "
            "Intentional increases require approval and then "
            "`uv run python scripts/check_complexity_budgets.py --update-baselines "
            "--baseline-sha $(git rev-parse HEAD)`."
        )
    return measurement


def check_prompt_sizes(budget: dict[str, Any]) -> dict[str, dict[str, int]]:
    measurements = measure_prompt_sizes()
    limits = budget["prompt_size"]["limits"]
    failures: list[str] = []
    for scenario, scenario_limits in limits.items():
        current = measurements.get(scenario)
        if current is None:
            failures.append(f"{scenario}: missing current measurement")
            continue
        for metric, limit in scenario_limits.items():
            if metric == "fitted_tokens":
                continue
            current_value = current[metric]
            if current_value > int(limit):
                failures.append(
                    f"{scenario}.{metric}: current={current_value}, "
                    f"limit={limit}, delta=+{current_value - int(limit)}"
                )
    if failures:
        failure_text = "; ".join(failures)
        raise BudgetFailure(
            "Prompt-size budget exceeded: "
            f"{failure_text}. Intentional increases require approval and then "
            "`uv run python scripts/check_complexity_budgets.py --update-baselines "
            "--baseline-sha $(git rev-parse HEAD)`."
        )
    return measurements


def _resolve_baseline_sha(value: str | None) -> str:
    if value:
        return value
    return _run_git(["rev-parse", "HEAD"]).strip()


def _print_success(
    *,
    budget: dict[str, Any],
    loc: SourceLocMeasurement | None,
    prompt_sizes: dict[str, dict[str, int]] | None,
) -> None:
    print(f"Baseline SHA: {budget['baseline_sha']}")
    if loc is not None:
        print(
            "Source LoC: "
            f"{loc.line_count}/{budget['source_loc']['limit']} "
            f"({loc.file_count} files)"
        )
    if prompt_sizes is not None:
        print("Prompt sizes:")
        limits = budget["prompt_size"]["limits"]
        for scenario in sorted(prompt_sizes):
            metrics = prompt_sizes[scenario]
            scenario_limits = limits[scenario]
            print(
                f"  {scenario}: "
                f"system={metrics['system_bytes']}/{scenario_limits['system_bytes']} bytes, "
                f"user={metrics['user_bytes']}/{scenario_limits['user_bytes']} bytes, "
                f"tools={metrics['tools_bytes']}/{scenario_limits['tools_bytes']} bytes, "
                f"total={metrics['total_bytes']}/{scenario_limits['total_bytes']} bytes"
            )
    print("Complexity budgets passed.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--loc-only", action="store_true", help="Check or update only the source LoC budget.")
    mode.add_argument("--prompt-only", action="store_true", help="Check or update only prompt-size budgets.")
    parser.add_argument("--update-baselines", action="store_true", help="Rewrite budgets to current measurements.")
    parser.add_argument("--baseline-sha", help="Baseline SHA to record when updating budgets.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.update_baselines:
            budget = update_baselines(
                baseline_sha=_resolve_baseline_sha(args.baseline_sha),
                loc_only=args.loc_only,
                prompt_only=args.prompt_only,
            )
            print(f"Updated {BUDGET_PATH.relative_to(REPO_ROOT)}")
            _print_success(budget=budget, loc=None, prompt_sizes=None)
            return 0

        budget = _load_budget()
        loc = None if args.prompt_only else check_source_loc(budget)
        prompt_sizes = None if args.loc_only else check_prompt_sizes(budget)
        _print_success(budget=budget, loc=loc, prompt_sizes=prompt_sizes)
        return 0
    except BudgetFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
