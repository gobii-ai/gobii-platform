import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from decimal import Decimal

import zstandard as zstd
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone

from api.agent.core.daily_limit_mode import DAILY_LIMIT_MESSAGE_TOOL_NAMES
from api.evals.base import EvalScenario, ScenarioTask
from api.evals.execution import ScenarioExecutionTools
from api.evals.registry import register_scenario
from api.models import (
    EvalRunTask,
    Organization,
    PersistentAgent,
    PersistentAgentPromptArchive,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    TaskCredit,
)
from api.services.daily_credit_settings import DailyCreditSettings
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNames
from util.urls import build_agent_detail_url, build_site_url

_daily_credit_settings_override = ContextVar("daily_credit_settings_override", default=None)
_daily_credit_settings_override_installed = False
_original_daily_credit_settings_for_owner = None


def _install_daily_credit_settings_override() -> None:
    global _daily_credit_settings_override_installed, _original_daily_credit_settings_for_owner
    if _daily_credit_settings_override_installed:
        return

    from api.agent.core import prompt_context
    from api.services import daily_credit_settings

    _original_daily_credit_settings_for_owner = daily_credit_settings.get_daily_credit_settings_for_owner

    def get_daily_credit_settings_for_owner(owner):
        settings = _daily_credit_settings_override.get()
        if settings is not None:
            return settings
        return _original_daily_credit_settings_for_owner(owner)

    daily_credit_settings.get_daily_credit_settings_for_owner = get_daily_credit_settings_for_owner
    prompt_context.get_daily_credit_settings_for_owner = get_daily_credit_settings_for_owner
    _daily_credit_settings_override_installed = True


DAILY_CREDIT_PROMPT_SUITE_SLUG = "daily_credit_prompt"
DAILY_CREDIT_PROMPT_NOT_NEAR_LIMIT = "daily_credit_prompt_not_near_limit"
DAILY_CREDIT_PROMPT_NEAR_LIMIT = "daily_credit_prompt_near_limit"
DAILY_CREDIT_PROMPT_ONE_TOOL_LEFT = "daily_credit_prompt_one_tool_left"
DAILY_CREDIT_PROMPT_SOFT_TARGET_DISTINCT = "daily_credit_prompt_soft_target_distinct"
DAILY_CREDIT_PROMPT_HARD_LIMIT_HIT = "daily_credit_prompt_hard_limit_hit"
DAILY_CREDIT_PROMPT_SCENARIO_SLUGS = [
    DAILY_CREDIT_PROMPT_NOT_NEAR_LIMIT,
    DAILY_CREDIT_PROMPT_NEAR_LIMIT,
    DAILY_CREDIT_PROMPT_ONE_TOOL_LEFT,
    DAILY_CREDIT_PROMPT_SOFT_TARGET_DISTINCT,
    DAILY_CREDIT_PROMPT_HARD_LIMIT_HIT,
]


class DailyCreditPromptScenario(EvalScenario, ScenarioExecutionTools):
    tier = "core"
    category = "prompt_policy"
    expected_runtime = "short"
    cost_class = "low"
    owner = "agent-platform"
    area = "agent_behavior"
    tags = ("agent_behavior", "micro", "prompt_policy", "daily_credit")
    tasks = [
        ScenarioTask(name="inject_prompt", assertion_type="manual"),
        ScenarioTask(name="verify_prompt_archive", assertion_type="exact_match"),
    ]
    daily_credit_limit = Decimal("100")
    hard_limit_multiplier = Decimal("1")
    usage_today = Decimal("0")
    user_prompt = (
        "Please draft a concise three-bullet status update I can send to my operations lead "
        "about onboarding progress and stop after replying."
    )

    def required_prompt_snippets(self, agent_id: str) -> tuple[str, ...]:
        return ()

    def forbidden_prompt_snippets(self) -> tuple[str, ...]:
        return ()

    def run(self, run_id: str, agent_id: str) -> None:
        self._ready_agent(agent_id)
        self._configure_daily_credit_state(run_id, agent_id)

        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="inject_prompt")
        with self._mock_daily_credit_settings(), self.wait_for_agent_idle(agent_id, timeout=120):
            inbound = self.inject_message(
                agent_id,
                self.user_prompt,
                trigger_processing=True,
                eval_run_id=run_id,
                eval_stop_policy={
                    "stop_on_tool_names_after_finish": ["send_chat_message"],
                    "max_relevant_tool_calls": 2,
                },
            )
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="inject_prompt",
            observed_summary="Prompt injected through the agent harness and processing completed.",
            artifacts={"message": inbound},
        )
        self._record_prompt_archive_expectations(run_id, agent_id, after=inbound.timestamp)

    def _ready_agent(self, agent_id: str) -> None:
        PersistentAgent.objects.filter(id=agent_id).update(
            execution_environment="eval",
            charter="Handle the user's work.",
            planning_state=PersistentAgent.PlanningState.SKIPPED,
        )
        if PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        ).exists():
            return
        step = PersistentAgentStep.objects.create(agent_id=agent_id, description="Process events")
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        )

    def _configure_daily_credit_state(self, run_id: str, agent_id: str) -> None:
        agent = PersistentAgent.objects.select_related("user").get(id=agent_id)
        suffix = str(run_id).replace("-", "")[:16]
        organization = Organization.objects.create(
            name=f"Daily Credit Eval {suffix}",
            slug=f"daily-credit-eval-{suffix}",
            created_by=agent.user,
        )
        organization.billing.purchased_seats = 1
        organization.billing.save(update_fields=["purchased_seats"])
        TaskCredit.objects.create(
            organization=organization,
            credits=Decimal("1000.000"),
            credits_used=Decimal("0.000"),
            granted_date=timezone.now(),
            expiration_date=timezone.now() + timedelta(days=30),
            plan=PlanNames.FREE,
            grant_type=GrantTypeChoices.COMPENSATION,
            comments="Daily credit prompt eval execution credits.",
        )
        PersistentAgent.objects.filter(id=agent_id).update(
            daily_credit_limit=int(self.daily_credit_limit),
            organization=organization,
        )
        if self.usage_today > Decimal("0"):
            step = PersistentAgentStep.objects.create(
                agent_id=agent_id,
                eval_run_id=run_id,
                description=f"Seeded eval daily credit usage: {self.usage_today}",
            )
            PersistentAgentStep.objects.filter(id=step.id).update(credits_cost=self.usage_today)

    @contextmanager
    def _mock_daily_credit_settings(self):
        _install_daily_credit_settings_override()
        settings = DailyCreditSettings(
            slider_min=Decimal("0"),
            slider_max=Decimal("50"),
            slider_step=Decimal("1"),
            default_daily_credit_target=int(self.daily_credit_limit),
            burn_rate_threshold_per_hour=Decimal("3"),
            offpeak_burn_rate_threshold_per_hour=Decimal("3"),
            burn_rate_window_minutes=60,
            burn_rate_threshold_24h=Decimal("0"),
            hard_limit_multiplier=self.hard_limit_multiplier,
        )
        token = _daily_credit_settings_override.set(settings)
        try:
            yield
        finally:
            _daily_credit_settings_override.reset(token)

    def _record_prompt_archive_expectations(self, run_id: str, agent_id: str, *, after) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_prompt_archive")
        archive, content = self._latest_prompt_archive_content(agent_id, after=after)
        if archive is None:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_prompt_archive",
                observed_summary="No prompt archive was produced by the agent run.",
            )
            return False

        missing = [snippet for snippet in self.required_prompt_snippets(agent_id) if snippet not in content]
        present_forbidden = [snippet for snippet in self.forbidden_prompt_snippets() if snippet in content]
        if missing or present_forbidden:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_prompt_archive",
                observed_summary=(
                    f"Missing expected snippet(s): {missing}; "
                    f"present forbidden snippet(s): {present_forbidden}."
                ),
                expected_summary="Audited prompt archive contains the expected daily-credit guidance.",
                artifacts={"prompt_excerpt": self._budget_excerpt(content), "prompt_archive_id": str(archive.id)},
            )
            return False

        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_prompt_archive",
            observed_summary="Audited prompt archive matched daily-credit guidance expectations.",
            expected_summary="Audited prompt archive contains the expected daily-credit guidance.",
            artifacts={"prompt_excerpt": self._budget_excerpt(content), "prompt_archive_id": str(archive.id)},
        )
        return True

    def _latest_prompt_archive_content(
        self,
        agent_id: str,
        *,
        after,
    ) -> tuple[PersistentAgentPromptArchive | None, str]:
        archives = PersistentAgentPromptArchive.objects.filter(
            agent_id=agent_id,
            rendered_at__gte=after,
        ).order_by("rendered_at")
        for archive in archives:
            try:
                with default_storage.open(archive.storage_key, "rb") as handle:
                    payload = zstd.ZstdDecompressor().decompress(handle.read())
                data = json.loads(payload.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, zstd.ZstdError):
                continue
            return archive, "\n\n".join(
                str(data.get(key) or "")
                for key in ("system_prompt", "user_prompt")
            )
        return None, ""

    def _budget_excerpt(self, content: str) -> str:
        start = content.find("## Budget Awareness")
        if start < 0:
            return content[:1600]
        end = content.find("\n## ", start + 1)
        if end < 0:
            end = min(len(content), start + 2200)
        return content[start:end]


@register_scenario
class DailyCreditPromptNotNearLimitScenario(DailyCreditPromptScenario):
    slug = DAILY_CREDIT_PROMPT_NOT_NEAR_LIMIT
    description = (
        "When an agent is not near its equal daily limit, the audited prompt should show progress "
        "without fatigue or hard-limit mode warnings."
    )
    usage_today = Decimal("50")
    user_prompt = (
        "Can you draft a quick three-bullet project status update for our operations lead? "
        "Keep it concise and avoid doing extra research."
    )

    def required_prompt_snippets(self, agent_id: str) -> tuple[str, ...]:
        return ("Daily limit progress:", "50", "Remaining credits:")

    def forbidden_prompt_snippets(self) -> tuple[str, ...]:
        return (
            "Getting tired",
            "DAILY HARD LIMIT MODE",
            "Soft target progress",
            "you will not be stopped immediately",
        )


@register_scenario
class DailyCreditPromptNearLimitScenario(DailyCreditPromptScenario):
    slug = DAILY_CREDIT_PROMPT_NEAR_LIMIT
    description = "At 80% of an equal daily limit, the audited prompt should warn the agent it is getting tired."
    usage_today = Decimal("80")
    user_prompt = (
        "Please write a short customer-facing summary of today's integration progress. "
        "Do not start a broad investigation; just give me a useful draft."
    )

    def required_prompt_snippets(self, agent_id: str) -> tuple[str, ...]:
        return ("Daily limit progress:", "80", "Getting tired (80%+)")

    def forbidden_prompt_snippets(self) -> tuple[str, ...]:
        return ("Soft target progress", "Hard limit progress")


@register_scenario
class DailyCreditPromptSoftTargetDistinctScenario(DailyCreditPromptScenario):
    slug = DAILY_CREDIT_PROMPT_SOFT_TARGET_DISTINCT
    description = (
        "When the soft target is below the hard limit, the audited prompt should keep target "
        "and hard-limit guidance distinct."
    )
    daily_credit_limit = Decimal("10")
    hard_limit_multiplier = Decimal("2")
    usage_today = Decimal("12")
    user_prompt = (
        "Can you prepare a concise follow-up note for the recruiting coordinator summarizing "
        "where we stand and what the next action should be?"
    )

    def required_prompt_snippets(self, agent_id: str) -> tuple[str, ...]:
        return (
            "Soft target progress:",
            "Hard limit progress:",
            "Exceeding this target leaves less room before the enforced hard limit.",
        )

    def forbidden_prompt_snippets(self) -> tuple[str, ...]:
        return ("you will not be stopped immediately", "Getting tired")


@register_scenario
class DailyCreditPromptOneToolLeftScenario(DailyCreditPromptScenario):
    slug = DAILY_CREDIT_PROMPT_ONE_TOOL_LEFT
    description = (
        "When only the default task cost remains, the audited prompt should use the stronger "
        "one-tool-left warning."
    )
    usage_today = Decimal("99.6")
    user_prompt = (
        "Please turn the notes so far into a final concise handoff I can use before we pause work for today."
    )

    def required_prompt_snippets(self, agent_id: str) -> tuple[str, ...]:
        return ("Daily limit progress:", "99.6", "Almost out of energy", "one tool call left")

    def forbidden_prompt_snippets(self) -> tuple[str, ...]:
        return ("Getting tired", "DAILY HARD LIMIT MODE", "Soft target progress")


@register_scenario
class DailyCreditPromptHardLimitHitScenario(DailyCreditPromptScenario):
    slug = DAILY_CREDIT_PROMPT_HARD_LIMIT_HIT
    description = (
        "When the hard daily limit is hit, the audited run should enter message-only guidance "
        "and expose raise-limit links."
    )
    tasks = [
        *DailyCreditPromptScenario.tasks,
        ScenarioTask(name="verify_message_only_tools", assertion_type="exact_match"),
    ]

    def run(self, run_id: str, agent_id: str) -> None:
        super().run(run_id, agent_id)
        self._record_message_only_tool_usage(run_id)

    usage_today = Decimal("100")
    user_prompt = (
        "Please send me a quick note with the current handoff status and what you would do next "
        "once more budget is available."
    )

    def required_prompt_snippets(self, agent_id: str) -> tuple[str, ...]:
        settings_url = build_agent_detail_url(agent_id, None)
        double_limit_url_prefix = build_site_url(
            reverse(
                "agent_daily_limit_action",
                kwargs={"pk": agent_id, "action": "double"},
            )
        )
        unlimited_limit_url_prefix = build_site_url(
            reverse(
                "agent_daily_limit_action",
                kwargs={"pk": agent_id, "action": "unlimited"},
            )
        )
        return (
            "DAILY HARD LIMIT MODE",
            "Only message tools are available until the user raises the limit",
            settings_url,
            f"double {double_limit_url_prefix}?token=",
            f"unlimited {unlimited_limit_url_prefix}?token=",
            "Daily limit progress:",
        )

    def forbidden_prompt_snippets(self) -> tuple[str, ...]:
        return ("Soft target progress", "Hard limit progress")

    def _record_message_only_tool_usage(self, run_id: str) -> bool:
        self.record_task_result(run_id, None, EvalRunTask.Status.RUNNING, task_name="verify_message_only_tools")
        calls = list(
            PersistentAgentToolCall.objects.filter(step__eval_run_id=run_id)
            .select_related("step")
            .order_by("step__created_at", "step__id")
        )
        disallowed = [
            call for call in calls
            if call.tool_name not in DAILY_LIMIT_MESSAGE_TOOL_NAMES
        ]
        if disallowed:
            self.record_task_result(
                run_id,
                None,
                EvalRunTask.Status.FAILED,
                task_name="verify_message_only_tools",
                observed_summary=(
                    f"Hard-limit run used non-message tool(s): {[call.tool_name for call in disallowed]}."
                ),
                artifacts={"step": disallowed[0].step},
            )
            return False
        self.record_task_result(
            run_id,
            None,
            EvalRunTask.Status.PASSED,
            task_name="verify_message_only_tools",
            observed_summary="Hard-limit run used only message tools.",
        )
        return True
