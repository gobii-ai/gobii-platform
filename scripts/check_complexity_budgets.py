#!/usr/bin/env python3
"""Check committed source LoC and rendered prompt-size budgets."""

import argparse
import json
import logging
import os
import subprocess
import sys
import warnings
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class SourceLocMeasurement:
    line_count: int
    file_count: int


@dataclass(frozen=True)
class PromptScenario:
    name: str
    is_first_run: bool = False
    planning: bool = False
    web_session: bool = False
    enabled_system_skill_keys: tuple[str, ...] = ()
    enabled_builtin_tool_names: tuple[str, ...] = ()
    inbound_message: str = ""
    expects_billing_catalog: bool = False
    mature_state: bool = False


TOOL_RICH_BUILTIN_COUNT = 7
PROMPT_SCENARIOS = (
    PromptScenario(name="normal_explicit_send"),
    PromptScenario(
        name="billing_catalog_request",
        inbound_message="What plans and add-ons are available, and what do they cost?",
        expects_billing_catalog=True,
    ),
    PromptScenario(name="normal_first_run", is_first_run=True),
    PromptScenario(name="web_chat_implied_send", web_session=True),
    PromptScenario(name="web_chat_first_run", is_first_run=True, web_session=True),
    PromptScenario(name="planning_first_run", is_first_run=True, planning=True),
    PromptScenario(name="planning_continuation", planning=True),
    PromptScenario(
        name="enabled_system_skills",
    ),
    PromptScenario(
        name="builtin_tool_rich",
    ),
    PromptScenario(name="mature_agent_state", mature_state=True),
)
PROMPT_SCENARIO_DESCRIPTIONS = {
    "billing_catalog_request": "Current unanswered user request asks for the conditional plan/add-on catalog.",
    "normal_explicit_send": "No active web session; communication requires explicit send tools.",
    "normal_first_run": "First execution run without an active web session.",
    "web_chat_implied_send": "Active web-chat session; text replies can use implied send.",
    "web_chat_first_run": "First execution run with an active web-chat session.",
    "planning_first_run": "Planning mode on the first run with a verified preferred contact.",
    "planning_continuation": "Planning mode after the first run.",
    "enabled_system_skills": "The three largest registered system-skill prompt/tool payloads, selected dynamically.",
    "builtin_tool_rich": "The seven largest available registered built-ins, selected dynamically.",
    "mature_agent_state": "Long legacy charter, six-step plan, and many saved skills after sustained use.",
}
PROMPT_BYTE_METRICS = (
    "system_bytes",
    "user_bytes",
    "tools_bytes",
    "total_bytes",
)


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
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
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


def _count_nonblank_lines(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    return sum(1 for line in text.splitlines() if line.strip())


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


def _configure_prompt_scenario(agent, scenario: PromptScenario) -> None:
    from api.agent.system_skills.service import enable_system_skills
    from api.models import PersistentAgentEnabledTool

    if scenario.inbound_message:
        from api.models import PersistentAgentMessage

        agent_endpoint = agent.comms_endpoints.filter(is_primary=True).first()
        PersistentAgentMessage.objects.create(
            owner_agent=agent,
            is_outbound=False,
            from_endpoint=agent.preferred_contact_endpoint,
            to_endpoint=agent_endpoint,
            body=scenario.inbound_message,
        )

    if scenario.enabled_system_skill_keys:
        result = enable_system_skills(agent, scenario.enabled_system_skill_keys)
        if result.get("invalid"):
            raise BudgetFailure(
                f"{scenario.name}: failed to enable representative system skills: "
                + ", ".join(result["invalid"])
            )

    if scenario.enabled_builtin_tool_names:
        PersistentAgentEnabledTool.objects.bulk_create(
            [
                PersistentAgentEnabledTool(
                    agent=agent,
                    tool_full_name=tool_name,
                    tool_server="builtin",
                    tool_name=tool_name,
                    last_used_at=FIXED_PROMPT_NOW,
                )
                for tool_name in scenario.enabled_builtin_tool_names
            ]
        )

    if scenario.mature_state:
        from api.models import PersistentAgentKanbanCard, PersistentAgentSkill

        agent.charter = " ".join(
            f"Durable rule {index}: preserve verified source links, exact filters, and correction {index}."
            for index in range(70)
        )
        agent.save(update_fields=["charter", "updated_at"])
        for index in range(20):
            PersistentAgentSkill.objects.create(
                agent=agent,
                name=f"mature-skill-{index:02d}",
                description="Reusable mature-agent workflow.",
                version=1,
                tools=["sqlite_batch"],
                instructions=(
                    f"Workflow {index}: use a bounded query, retain source URLs, checkpoint progress, "
                    "and apply later corrections without discarding unrelated constraints."
                ),
                last_used_at=FIXED_PROMPT_NOW - timedelta(minutes=index),
            )
        PersistentAgentKanbanCard.objects.bulk_create(
            [
                PersistentAgentKanbanCard(
                    assigned_agent=agent,
                    title=(f"Mature plan step {index}: " + "verify, transform, and checkpoint evidence " * 6)[:255],
                    status="doing" if index == 0 else "todo",
                    priority=6 - index,
                )
                for index in range(6)
            ]
        )


def _measure_prompt_scenario(
    *,
    scenario: PromptScenario,
) -> dict[str, int]:
    from api.agent.core.prompt_context import build_prompt_context_preview, get_agent_tools
    from api.services.web_sessions import start_web_session

    agent, user, _endpoint = _create_agent(
        scenario=scenario.name,
        planning=scenario.planning,
    )
    if scenario.name == "enabled_system_skills":
        scenario = replace(
            scenario,
            enabled_system_skill_keys=_largest_system_skill_keys(agent, limit=3),
        )
    elif scenario.name == "builtin_tool_rich":
        scenario = replace(
            scenario,
            enabled_builtin_tool_names=_largest_builtin_tool_names(
                agent,
                limit=TOOL_RICH_BUILTIN_COUNT,
            ),
        )
    _configure_prompt_scenario(agent, scenario)
    if scenario.web_session:
        start_web_session(agent, user)

    messages, _fitted_tokens, _metadata = build_prompt_context_preview(
        agent,
        is_first_run=scenario.is_first_run,
        daily_credit_state=_daily_credit_state(),
        prefer_low_latency=scenario.web_session,
    )
    unexpected_roles = sorted({message["role"] for message in messages} - {"system", "user"})
    if unexpected_roles:
        raise BudgetFailure(f"{scenario.name}: unmeasured prompt roles: {', '.join(unexpected_roles)}")
    system_messages = [message["content"] for message in messages if message["role"] == "system"]
    user_messages = [message["content"] for message in messages if message["role"] == "user"]
    if not system_messages or not user_messages:
        raise BudgetFailure(f"{scenario.name}: expected system and user prompt messages")
    system_message = "".join(system_messages)
    user_message = "".join(user_messages)
    tools = get_agent_tools(agent)

    missing_skill_keys = [
        skill_key
        for skill_key in scenario.enabled_system_skill_keys
        if f"<skill_{skill_key}>" not in user_message
    ]
    if missing_skill_keys:
        raise BudgetFailure(
            f"{scenario.name}: expected system skills were not rendered: "
            + ", ".join(missing_skill_keys)
        )

    billing_catalog_present = "Available plans:" in user_message and "Available add-ons:" in user_message
    if billing_catalog_present != scenario.expects_billing_catalog:
        expectation = "include" if scenario.expects_billing_catalog else "omit"
        raise BudgetFailure(f"{scenario.name}: expected prompt to {expectation} the conditional billing catalog")

    from api.agent.system_skills.registry import get_system_skill_definition

    rendered_tool_names = {
        tool.get("function", {}).get("name")
        for tool in tools
        if isinstance(tool, dict)
    }
    skill_tool_names = {
        tool_name
        for skill_key in scenario.enabled_system_skill_keys
        for definition in [get_system_skill_definition(skill_key)]
        if definition is not None
        for tool_name in definition.tools_to_enable()
    }
    missing_tool_names = sorted(
        (set(scenario.enabled_builtin_tool_names) | skill_tool_names) - rendered_tool_names
    )
    if missing_tool_names:
        raise BudgetFailure(
            f"{scenario.name}: expected associated tools were not rendered: "
            + ", ".join(missing_tool_names)
        )

    system_bytes = _text_size(system_message)
    user_bytes = _text_size(user_message)
    tools_bytes = _json_size(tools)
    return {
        "system_bytes": system_bytes,
        "user_bytes": user_bytes,
        "tools_bytes": tools_bytes,
        "total_bytes": system_bytes + user_bytes + tools_bytes,
    }


def _largest_system_skill_keys(agent, *, limit: int) -> tuple[str, ...]:
    from api.agent.system_skills.registry import SYSTEM_SKILL_REGISTRY
    from api.agent.tools.tool_manager import BUILTIN_TOOL_REGISTRY

    payloads = []
    for definition in SYSTEM_SKILL_REGISTRY.values():
        prompt_payload = definition.render_prompt_instructions(agent) + definition.render_prompt_context(agent)
        tool_definitions = []
        for tool_name in definition.tools_to_enable():
            entry = BUILTIN_TOOL_REGISTRY.get(tool_name)
            if entry is None:
                continue
            tool_definitions.append(entry["definition"]())
        payloads.append(
            (
                _text_size(prompt_payload) + _json_size(tool_definitions),
                definition.skill_key,
            )
        )
    payloads.sort(key=lambda item: (-item[0], item[1]))
    selected = tuple(skill_key for _size, skill_key in payloads[:limit])
    if len(selected) != limit:
        raise BudgetFailure(f"Expected at least {limit} registered system skills, found {len(selected)}")
    return selected


def _largest_builtin_tool_names(agent, *, limit: int) -> tuple[str, ...]:
    from api.agent.tools.tool_manager import BUILTIN_TOOL_REGISTRY, _is_builtin_tool_available

    payloads = [
        (_json_size(entry["definition"]()), tool_name)
        for tool_name, entry in BUILTIN_TOOL_REGISTRY.items()
        if _is_builtin_tool_available(tool_name, agent, include_hidden=True)
    ]
    payloads.sort(key=lambda item: (-item[0], item[1]))
    selected = tuple(tool_name for _size, tool_name in payloads[:limit])
    if len(selected) != limit:
        raise BudgetFailure(f"Expected at least {limit} available built-ins, found {len(selected)}")
    return selected


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
                        scenario.name: _measure_prompt_scenario(scenario=scenario)
                        for scenario in PROMPT_SCENARIOS
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
            "description": "Nonblank lines in tracked and unignored core product source files, excluding tests and dedicated eval assets.",
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
        },
        "prompt_size": {
            "description": "Rendered prompt messages plus JSON tool definitions for representative send/planning paths.",
            "unit": "utf8_bytes",
            "limit_policy": (
                "Hard limits are approved manually. --update-baselines refreshes observed measurements "
                "but never changes limits."
            ),
            "scenarios": dict(PROMPT_SCENARIO_DESCRIPTIONS),
        },
    }


def _validate_prompt_scenario_coverage(
    measurements: dict[str, dict[str, int]],
    limits: dict[str, dict[str, int]],
) -> list[str]:
    failures: list[str] = []
    measurement_names = set(measurements)
    limit_names = set(limits)
    for scenario in sorted(limit_names - measurement_names):
        failures.append(f"{scenario}: missing current measurement")
    for scenario in sorted(measurement_names - limit_names):
        failures.append(f"{scenario}: missing approved limits")

    for scenario in sorted(measurement_names & limit_names):
        missing_measurements = set(PROMPT_BYTE_METRICS) - set(measurements[scenario])
        for metric in sorted(missing_measurements):
            failures.append(f"{scenario}.{metric}: missing current measurement")
        missing_metrics = set(PROMPT_BYTE_METRICS) - set(limits[scenario])
        for metric in sorted(missing_metrics):
            failures.append(f"{scenario}.{metric}: missing approved limit")
    return failures


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
        limits = existing.get("prompt_size", {}).get("limits")
        if not isinstance(limits, dict):
            raise BudgetFailure(
                "Approved prompt-size limits are missing. Add reviewed hard limits manually; "
                "--update-baselines never creates or relaxes them."
            )
        coverage_failures = _validate_prompt_scenario_coverage(prompt_sizes, limits)
        if coverage_failures:
            raise BudgetFailure(
                "Prompt-size observations were not updated because approved limit coverage is incomplete: "
                + "; ".join(coverage_failures)
            )
        budget["prompt_size"]["limits"] = limits
        budget["prompt_size"]["observed"] = prompt_sizes
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
    failures = _validate_prompt_scenario_coverage(measurements, limits)
    for scenario, scenario_limits in limits.items():
        current = measurements.get(scenario)
        if current is None:
            continue
        for metric in PROMPT_BYTE_METRICS:
            if metric not in scenario_limits or metric not in current:
                continue
            limit = scenario_limits[metric]
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
            "a manual hard-limit change. `--update-baselines` only refreshes observations."
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
    parser.add_argument(
        "--update-baselines",
        action="store_true",
        help="Refresh observations and source LoC; approved prompt limits are never changed.",
    )
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
            if not args.loc_only:
                print("Refreshed prompt observations; approved hard limits were not changed.")
            print("Run the checker without --update-baselines to verify the approved limits.")
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
