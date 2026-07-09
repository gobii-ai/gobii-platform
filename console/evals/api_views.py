import logging
import uuid
from typing import Any
from uuid import UUID

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from api.evals.catalog import serialize_scenario_catalog_item, scenario_to_suite_slugs
from api.evals.global_skill_evals import (
    GLOBAL_SKILL_EVAL_RUBRIC_VERSION, GLOBAL_SKILL_EVAL_SCENARIO_SLUG, GLOBAL_SKILL_EVAL_SUITE_SLUG, build_global_skill_eval_secret_status, build_skill_eval_summary, serialize_global_skill_eval_skill,
)
from api.evals.llm_routing_profile_snapshot import create_eval_profile_snapshot
from api.evals.owner import ensure_eval_runner_user_and_owner
from api.evals.realtime import broadcast_run_update, broadcast_suite_update
from api.evals.registry import ScenarioRegistry
from api.evals.runner import _update_suite_state
from api.evals.suites import SuiteRegistry
from api.evals.tasks import gc_eval_runs_task, run_eval_task
from api.models import BrowserUseAgent, EvalRun, EvalRunTask, EvalSuiteRun, GlobalAgentSkill, PersistentAgent
from console.api_helpers import _parse_json_body
from console.context_helpers import build_console_context
from util.urls import IMMERSIVE_APP_BASE_PATH

logger = logging.getLogger(__name__)


class SystemAdminAPIView(LoginRequiredMixin, View):
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any):
        if not (request.user.is_staff or request.user.is_superuser):
            return JsonResponse({"error": "forbidden"}, status=403)
        return super().dispatch(request, *args, **kwargs)


def _serialize_eval_task(task: EvalRunTask) -> dict[str, Any]:
    artifact_links = {
        "message_id": str(task.first_message_id) if task.first_message_id else None,
        "step_id": str(task.first_step_id) if task.first_step_id else None,
        "browser_task_id": str(task.first_browser_task_id) if task.first_browser_task_id else None,
        "agent_audit_url": (
            f"/console/staff/agents/{task.run.agent_id}/audit/"
            if task.run_id and task.run.agent_id
            else None
        ),
    }
    return {
        "id": task.id,
        "sequence": task.sequence,
        "name": task.name,
        "status": task.status,
        "assertion_type": task.assertion_type,
        "expected_summary": task.expected_summary,
        "observed_summary": task.observed_summary,
        "debug_artifacts": task.debug_artifacts or {},
        "artifact_links": artifact_links,
        "llm_question": task.llm_question,
        "llm_answer": task.llm_answer,
        "llm_model": task.llm_model,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "prompt_tokens": task.prompt_tokens,
        "completion_tokens": task.completion_tokens,
        "total_tokens": task.total_tokens,
        "cached_tokens": task.cached_tokens,
        "input_cost_total": float(task.input_cost_total),
        "input_cost_uncached": float(task.input_cost_uncached),
        "input_cost_cached": float(task.input_cost_cached),
        "output_cost": float(task.output_cost),
        "total_cost": float(task.total_cost),
        "credits_cost": float(task.credits_cost),
    }


def _task_counts(tasks: list[EvalRunTask]) -> dict[str, int | float | None]:
    totals: dict[str, int | float | None] = {
        "total": len(tasks),
        "completed": 0,
        "passed": 0,
        "failed": 0,
        "pass_rate": None,
    }
    for task in tasks:
        if task.status == EvalRunTask.Status.PASSED:
            totals["passed"] += 1
            totals["completed"] += 1
        elif task.status in (
            EvalRunTask.Status.FAILED,
            EvalRunTask.Status.ERRORED,
            EvalRunTask.Status.SKIPPED,
        ):
            totals["failed"] += 1
            totals["completed"] += 1
    if totals["completed"]:
        totals["pass_rate"] = totals["passed"] / totals["completed"]
    return totals


def _serialize_eval_run(run: EvalRun, *, include_tasks: bool = False) -> dict[str, Any]:
    tasks = list(run.tasks.all()) if include_tasks else []
    counts = _task_counts(tasks) if include_tasks else None

    payload: dict[str, Any] = {
        "id": str(run.id),
        "suite_run_id": str(run.suite_run_id) if run.suite_run_id else None,
        "scenario_slug": run.scenario_slug,
        "scenario_version": run.scenario_version,
        "scenario_fingerprint": run.scenario_fingerprint or None,
        "code_version": run.code_version or None,
        "code_branch": run.code_branch or None,
        "status": run.status,
        "run_type": run.run_type,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "agent_id": str(run.agent_id) if run.agent_id else None,
        "llm_routing_profile_name": run.llm_routing_profile_name or None,
        "primary_model": run.primary_model or None,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "cached_tokens": run.cached_tokens,
        "tokens_used": run.tokens_used,
        "input_cost_total": float(run.input_cost_total),
        "input_cost_uncached": float(run.input_cost_uncached),
        "input_cost_cached": float(run.input_cost_cached),
        "output_cost": float(run.output_cost),
        "total_cost": float(run.total_cost),
        "credits_cost": float(run.credits_cost),
        "completion_count": run.completion_count,
        "step_count": run.step_count,
    }

    if include_tasks:
        payload["tasks"] = [_serialize_eval_task(task) for task in tasks]
        payload["task_totals"] = counts

    return payload


def _resolve_console_secret_owner(request: HttpRequest):
    context = build_console_context(request)
    if context.current_context.type == "organization":
        membership = context.current_membership
        if membership is None or not context.can_manage_org_agents:
            raise PermissionDenied("You do not have permission to manage organization secrets.")
        return None, membership.org
    return request.user, None


def _create_eval_ephemeral_agent(
    *,
    label_suffix: str,
    eval_user,
    eval_organization,
) -> PersistentAgent:
    unique_id = f"{label_suffix}-{uuid.uuid4().hex[:8]}" if label_suffix else uuid.uuid4().hex[:12]
    browser_agent = BrowserUseAgent(name=f"Eval Browser {unique_id}", user=eval_user)
    browser_agent._agent_creation_organization = eval_organization
    browser_agent.save()
    return PersistentAgent.objects.create(
        name=f"Eval Agent {unique_id}",
        user=eval_user,
        organization=eval_organization,
        browser_use_agent=browser_agent,
        execution_environment="eval",
        charter="You are a test agent.",
    )


def _serialize_suite_run(suite: EvalSuiteRun, *, include_runs: bool = False, include_tasks: bool = False) -> dict[str, Any]:
    runs = list(suite.runs.all()) if include_runs else []
    runs_payload = [_serialize_eval_run(run, include_tasks=include_tasks) for run in runs] if include_runs else []

    suite_task_totals = None
    if include_runs:
        all_tasks: list[EvalRunTask] = []
        for run in runs:
            all_tasks.extend(list(run.tasks.all()))
        suite_task_totals = _task_counts(all_tasks)

    aggregate_counts = {"total_runs": len(runs), "completed": 0, "errored": 0}
    for run in runs:
        if run.status == EvalRun.Status.COMPLETED:
            aggregate_counts["completed"] += 1
        elif run.status == EvalRun.Status.ERRORED:
            aggregate_counts["errored"] += 1

    cost_totals = None
    if include_runs:
        cost_totals = {
            "prompt_tokens": sum(r.prompt_tokens for r in runs),
            "completion_tokens": sum(r.completion_tokens for r in runs),
            "cached_tokens": sum(r.cached_tokens for r in runs),
            "tokens_used": sum(r.tokens_used for r in runs),
            "input_cost_total": float(sum(r.input_cost_total for r in runs)),
            "input_cost_uncached": float(sum(r.input_cost_uncached for r in runs)),
            "input_cost_cached": float(sum(r.input_cost_cached for r in runs)),
            "output_cost": float(sum(r.output_cost for r in runs)),
            "total_cost": float(sum(r.total_cost for r in runs)),
            "credits_cost": float(sum(r.credits_cost for r in runs)),
        }

    # Serialize the LLM routing profile if present
    llm_routing_profile = None
    if suite.llm_routing_profile_id:
        from console.llm_serializers import get_routing_profile_with_prefetch, serialize_routing_profile_detail
        try:
            profile = get_routing_profile_with_prefetch(str(suite.llm_routing_profile_id))
            llm_routing_profile = serialize_routing_profile_detail(profile)
        except Exception:
            # Fallback to basic info if prefetch fails
            llm_routing_profile = {
                "id": str(suite.llm_routing_profile_id),
                "name": suite.llm_routing_profile.name if suite.llm_routing_profile else None,
                "display_name": suite.llm_routing_profile.display_name if suite.llm_routing_profile else None,
            }

    skill_eval = None
    display_name = suite.suite_slug
    if suite.launcher_type == EvalSuiteRun.LauncherType.GLOBAL_SKILL:
        skill_eval = build_skill_eval_summary(suite.launch_config)
        if skill_eval and skill_eval.get("global_skill_name"):
            display_name = str(skill_eval["global_skill_name"])

    return {
        "id": str(suite.id),
        "suite_slug": suite.suite_slug,
        "launcher_type": suite.launcher_type,
        "display_name": display_name,
        "skill_eval": skill_eval,
        "status": suite.status,
        "run_type": suite.run_type,
        "requested_runs": suite.requested_runs,
        "agent_strategy": suite.agent_strategy,
        "shared_agent_id": str(suite.shared_agent_id) if suite.shared_agent_id else None,
        "started_at": suite.started_at.isoformat() if suite.started_at else None,
        "finished_at": suite.finished_at.isoformat() if suite.finished_at else None,
        "runs": runs_payload if include_runs else None,
        "run_totals": aggregate_counts if include_runs else None,
        "task_totals": suite_task_totals if include_runs else None,
        "cost_totals": cost_totals if include_runs else None,
        "llm_routing_profile": llm_routing_profile,
    }


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteListAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        suites = []
        for suite in sorted(SuiteRegistry.list_all().values(), key=lambda s: s.slug):
            suites.append(
                {
                    "slug": suite.slug,
                    "description": suite.description,
                    "scenario_slugs": list(suite.scenario_slugs),
                }
            )
        suite_mapping = scenario_to_suite_slugs()
        scenarios = [
            serialize_scenario_catalog_item(
                scenario,
                suite_slugs=suite_mapping.get(slug, []),
            )
            for slug, scenario in sorted(ScenarioRegistry.list_all().items())
        ]
        return JsonResponse({"suites": suites, "scenarios": scenarios})


@method_decorator(csrf_exempt, name="dispatch")
class GlobalSkillEvalLauncherAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        owner_user, owner_org = _resolve_console_secret_owner(request)
        global_skills = [
            serialize_global_skill_eval_skill(
                skill,
                owner_user=owner_user,
                owner_org=owner_org,
            )
            for skill in GlobalAgentSkill.objects.filter(is_active=True)
            .prefetch_related("bundled_custom_tools")
            .order_by("name")
        ]
        return JsonResponse(
            {
                "global_skills": global_skills,
                "rubric_version": GLOBAL_SKILL_EVAL_RUBRIC_VERSION,
                "global_secrets_url": f"{IMMERSIVE_APP_BASE_PATH}/secrets",
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class GlobalSkillEvalRunCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        MAX_REQUESTED_RUNS = 10

        try:
            body = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        global_skill_id = body.get("global_skill_id")
        if not global_skill_id:
            return HttpResponseBadRequest("global_skill_id is required")
        try:
            normalized_global_skill_id = UUID(str(global_skill_id))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("global_skill_id must be a valid UUID")

        task_prompt = str(body.get("task_prompt") or "").strip()
        if not task_prompt:
            return HttpResponseBadRequest("task_prompt is required")

        n_runs_raw = body.get("n_runs") if "n_runs" in body else body.get("runs")
        if n_runs_raw is None:
            requested_runs = 1
        else:
            try:
                requested_runs = int(n_runs_raw)
            except (TypeError, ValueError):
                return HttpResponseBadRequest(f"n_runs must be an integer between 1 and {MAX_REQUESTED_RUNS}")
        if requested_runs < 1 or requested_runs > MAX_REQUESTED_RUNS:
            return HttpResponseBadRequest(f"n_runs must be between 1 and {MAX_REQUESTED_RUNS}")

        skill = get_object_or_404(
            GlobalAgentSkill.objects.filter(is_active=True).prefetch_related("bundled_custom_tools"),
            pk=normalized_global_skill_id,
        )

        owner_user, owner_org = _resolve_console_secret_owner(request)
        secret_status = build_global_skill_eval_secret_status(
            skill,
            owner_user=owner_user,
            owner_org=owner_org,
        )
        if not secret_status["launchable"]:
            return JsonResponse(
                {
                    "error": "Missing required global secrets for this skill eval.",
                    "missing_required_secrets": secret_status["missing_required_secrets"],
                },
                status=400,
            )

        from api.models import LLMRoutingProfile

        source_routing_profile = None
        llm_routing_profile_id = body.get("llm_routing_profile_id")
        if llm_routing_profile_id:
            try:
                normalized_routing_profile_id = UUID(str(llm_routing_profile_id))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("LLM routing profile not found")
            try:
                source_routing_profile = LLMRoutingProfile.objects.get(
                    id=normalized_routing_profile_id,
                    is_eval_snapshot=False,
                )
            except LLMRoutingProfile.DoesNotExist:
                return HttpResponseBadRequest("LLM routing profile not found")

        suite_run_id = uuid.uuid4()
        profile_snapshot = None
        if source_routing_profile:
            profile_snapshot = create_eval_profile_snapshot(
                source_routing_profile,
                str(suite_run_id),
            )

        launch_config = {
            "global_skill_id": str(skill.id),
            "global_skill_name": skill.name,
            "task_prompt": task_prompt,
            "rubric_version": GLOBAL_SKILL_EVAL_RUBRIC_VERSION,
            "required_secret_status": secret_status["required_secret_status"],
            "effective_tool_ids": list(skill.get_effective_tool_ids()),
        }

        suite_run = EvalSuiteRun.objects.create(
            id=suite_run_id,
            suite_slug=GLOBAL_SKILL_EVAL_SUITE_SLUG,
            launcher_type=EvalSuiteRun.LauncherType.GLOBAL_SKILL,
            launch_config=launch_config,
            initiated_by=request.user,
            status=EvalSuiteRun.Status.RUNNING,
            run_type=EvalSuiteRun.RunType.ONE_OFF,
            requested_runs=requested_runs,
            agent_strategy=EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO,
            started_at=timezone.now(),
            llm_routing_profile=profile_snapshot,
        )

        eval_user, eval_organization = ensure_eval_runner_user_and_owner(
            minimum_seats=max(1, requested_runs),
        )
        created_runs: list[EvalRun] = []
        for iteration in range(requested_runs):
            suffix = f"{skill.name[:8]}-{iteration + 1}" if requested_runs > 1 else skill.name[:8]
            run_agent = _create_eval_ephemeral_agent(
                label_suffix=suffix,
                eval_user=eval_user,
                eval_organization=eval_organization,
            )
            run = EvalRun.objects.create(
                suite_run=suite_run,
                scenario_slug=GLOBAL_SKILL_EVAL_SCENARIO_SLUG,
                scenario_version="1.0.0",
                agent=run_agent,
                initiated_by=request.user,
                status=EvalRun.Status.PENDING,
                run_type=EvalSuiteRun.RunType.ONE_OFF,
            )
            run_eval_task.delay(str(run.id))
            created_runs.append(run)

        _update_suite_state(suite_run.id)
        suite_run.refresh_from_db()

        try:
            gc_eval_runs_task.delay()
        except Exception:
            logger.debug("Failed to enqueue eval GC task", exc_info=True)

        return JsonResponse(
            {
                "suite_runs": [_serialize_suite_run(suite_run, include_runs=True, include_tasks=False)],
                "agent_strategy": EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO,
                "runs": [str(run.id) for run in created_runs],
            },
            status=201,
        )


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteRunCreateAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any):
        MAX_REQUESTED_RUNS = 10

        try:
            body = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        suite_slugs = body.get("suite_slugs")
        scenario_slugs = body.get("scenario_slugs") or []
        if suite_slugs is None and not scenario_slugs:
            suite_slugs = ["all"]
        if suite_slugs is not None and (not isinstance(suite_slugs, list) or not suite_slugs):
            return HttpResponseBadRequest("suite_slugs must be a non-empty list")
        if not isinstance(scenario_slugs, list):
            return HttpResponseBadRequest("scenario_slugs must be a list")

        suite_specs = []
        for suite_slug in suite_slugs or []:
            suite_obj = SuiteRegistry.get(str(suite_slug))
            if not suite_obj:
                return HttpResponseBadRequest(f"Suite '{suite_slug}' not found")
            suite_specs.append((suite_obj.slug, list(dict.fromkeys(suite_obj.scenario_slugs))))
        for scenario_slug in scenario_slugs:
            scenario = ScenarioRegistry.get(str(scenario_slug))
            if not scenario:
                return HttpResponseBadRequest(f"Scenario '{scenario_slug}' not found")
            suite_specs.append((f"single::{scenario.slug}", [scenario.slug]))

        agent_strategy = body.get("agent_strategy") or EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO
        if agent_strategy not in dict(EvalSuiteRun.AgentStrategy.choices):
            return HttpResponseBadRequest("Invalid agent_strategy")

        shared_agent: PersistentAgent | None = None
        run_type_raw = body.get("run_type") or EvalSuiteRun.RunType.ONE_OFF
        if isinstance(body.get("official"), bool):
            run_type_raw = EvalSuiteRun.RunType.OFFICIAL if body.get("official") else EvalSuiteRun.RunType.ONE_OFF
        if isinstance(run_type_raw, str):
            run_type_raw = run_type_raw.lower()
        if run_type_raw not in dict(EvalSuiteRun.RunType.choices):
            return HttpResponseBadRequest("Invalid run_type")
        run_type: str = run_type_raw

        n_runs_raw = body.get("n_runs") if "n_runs" in body else body.get("runs")
        if n_runs_raw is None:
            requested_runs = 3
        else:
            try:
                requested_runs = int(n_runs_raw)
            except (TypeError, ValueError):
                return HttpResponseBadRequest(f"n_runs must be an integer between 1 and {MAX_REQUESTED_RUNS}")
        if requested_runs < 1 or requested_runs > MAX_REQUESTED_RUNS:
            return HttpResponseBadRequest(f"n_runs must be between 1 and {MAX_REQUESTED_RUNS}")

        # Optional LLM routing profile for the eval
        from api.models import LLMRoutingProfile
        source_routing_profile = None
        llm_routing_profile_id = body.get("llm_routing_profile_id")
        if llm_routing_profile_id:
            try:
                source_routing_profile = LLMRoutingProfile.objects.get(
                    id=llm_routing_profile_id,
                    is_eval_snapshot=False,  # Don't allow selecting an existing snapshot
                )
            except LLMRoutingProfile.DoesNotExist:
                return HttpResponseBadRequest("LLM routing profile not found")

        agent_id = body.get("agent_id")
        if agent_strategy == EvalSuiteRun.AgentStrategy.REUSE_AGENT:
            if not agent_id:
                return HttpResponseBadRequest("agent_id is required when reusing an agent")
            try:
                shared_agent = PersistentAgent.objects.get(id=agent_id)
            except PersistentAgent.DoesNotExist:
                return HttpResponseBadRequest("Agent not found")
            if shared_agent.organization_id is not None:
                personal_agent_scenarios = [
                    scenario_slug
                    for _suite_slug, scenario_slugs in suite_specs
                    for scenario_slug in scenario_slugs
                    if getattr(ScenarioRegistry.get(scenario_slug), "requires_personal_agent", False)
                ]
                if personal_agent_scenarios:
                    scenario_list = ", ".join(dict.fromkeys(personal_agent_scenarios))
                    return HttpResponseBadRequest(
                        "agent_strategy=reuse_agent cannot use an organization-owned agent "
                        f"for personal-agent scenario(s): {scenario_list}"
                    )

        total_ephemeral_run_count = 0
        if agent_strategy == EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO:
            total_ephemeral_run_count = sum(len(scenario_slugs) for _suite_slug, scenario_slugs in suite_specs)
            total_ephemeral_run_count *= requested_runs
        eval_user = None
        eval_organization = None
        if total_ephemeral_run_count:
            eval_user, eval_organization = ensure_eval_runner_user_and_owner(
                minimum_seats=max(1, total_ephemeral_run_count),
            )

        created_suite_runs: list[EvalSuiteRun] = []
        created_runs: list[EvalRun] = []

        for suite_slug, scenario_slugs in suite_specs:
            # Create a temporary suite run ID to use for snapshot naming
            temp_suite_run_id = uuid.uuid4()

            # Create a snapshot of the profile if one was specified
            profile_snapshot = None
            if source_routing_profile:
                profile_snapshot = create_eval_profile_snapshot(
                    source_routing_profile,
                    str(temp_suite_run_id),
                )

            suite_run = EvalSuiteRun.objects.create(
                id=temp_suite_run_id,
                suite_slug=suite_slug,
                initiated_by=request.user,
                status=EvalSuiteRun.Status.RUNNING,
                run_type=run_type,
                requested_runs=requested_runs,
                agent_strategy=agent_strategy,
                shared_agent=shared_agent if agent_strategy == EvalSuiteRun.AgentStrategy.REUSE_AGENT else None,
                started_at=timezone.now(),
                llm_routing_profile=profile_snapshot,
            )

            created_for_suite = 0
            for scenario_slug in scenario_slugs:
                scenario = ScenarioRegistry.get(scenario_slug)
                if not scenario:
                    continue

                for iteration in range(requested_runs):
                    run_agent = shared_agent
                    if agent_strategy == EvalSuiteRun.AgentStrategy.EPHEMERAL_PER_SCENARIO or run_agent is None:
                        suffix = f"{scenario.slug[:8]}-{iteration + 1}" if requested_runs > 1 else scenario.slug[:8]
                        scenario_eval_organization = (
                            None
                            if getattr(scenario, "requires_personal_agent", False)
                            else eval_organization
                        )
                        run_agent = _create_eval_ephemeral_agent(
                            label_suffix=suffix,
                            eval_user=eval_user,
                            eval_organization=scenario_eval_organization,
                        )

                    run = EvalRun.objects.create(
                        suite_run=suite_run,
                        scenario_slug=scenario.slug,
                        scenario_version=getattr(scenario, "version", "") or "",
                        agent=run_agent,
                        initiated_by=request.user,
                        status=EvalRun.Status.PENDING,
                        run_type=run_type,
                    )
                    run_eval_task.delay(str(run.id))
                    created_runs.append(run)
                    created_for_suite += 1

            if created_for_suite == 0:
                suite_run.status = EvalSuiteRun.Status.ERRORED
                suite_run.finished_at = timezone.now()
                suite_run.save(update_fields=["status", "finished_at", "updated_at"])
            created_suite_runs.append(suite_run)

        # Update suite aggregate state and return payload
        response_suites = []
        for suite_run in created_suite_runs:
            _update_suite_state(suite_run.id)
            suite_run.refresh_from_db()
            response_suites.append(_serialize_suite_run(suite_run, include_runs=True, include_tasks=False))

        # Trigger background GC to clean up any stale runs
        try:
            gc_eval_runs_task.delay()
        except Exception:
            logger.debug("Failed to enqueue eval GC task", exc_info=True)

        return JsonResponse(
            {
                "suite_runs": response_suites,
                "agent_strategy": agent_strategy,
                "runs": [str(run.id) for run in created_runs],
            },
            status=201,
        )


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteRunListAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        status_filter = request.GET.get("status")
        suite_filter = request.GET.get("suite")
        run_type_filter = request.GET.get("run_type")
        limit_raw = request.GET.get("limit") or "25"
        try:
            limit = max(1, min(100, int(limit_raw)))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")
        if run_type_filter:
            run_type_filter = run_type_filter.lower()
            if run_type_filter not in dict(EvalSuiteRun.RunType.choices):
                return HttpResponseBadRequest("Invalid run_type")

        qs = (
            EvalSuiteRun.objects.select_related("initiated_by", "shared_agent")
            .prefetch_related("runs__tasks")
        )
        if status_filter:
            qs = qs.filter(status=status_filter)
        if suite_filter:
            qs = qs.filter(suite_slug=suite_filter)
        if run_type_filter:
            qs = qs.filter(run_type=run_type_filter)

        suite_runs = list(qs.order_by("-created_at")[:limit])
        # Refresh stale aggregates so UI doesn't show stuck "running" rows
        for suite in suite_runs:
            _update_suite_state(suite.id)

        suite_runs = list(
            EvalSuiteRun.objects.filter(id__in=[suite.id for suite in suite_runs])
            .select_related("initiated_by", "shared_agent")
            .prefetch_related("runs__tasks")
            .order_by("-created_at")
        )
        payload = [_serialize_suite_run(suite, include_runs=True, include_tasks=False) for suite in suite_runs]
        return JsonResponse({"suite_runs": payload})


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteRunDetailAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, suite_run_id: str, *args: Any, **kwargs: Any):
        _update_suite_state(suite_run_id)
        suite = get_object_or_404(
            EvalSuiteRun.objects.prefetch_related("runs__tasks", "runs__agent"),
            pk=suite_run_id,
        )
        return JsonResponse({"suite_run": _serialize_suite_run(suite, include_runs=True, include_tasks=True)})


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteRunRunTypeAPIView(SystemAdminAPIView):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, suite_run_id: str, *args: Any, **kwargs: Any):
        try:
            body = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        run_type_raw = body.get("run_type")
        if isinstance(body.get("official"), bool):
            run_type_raw = EvalSuiteRun.RunType.OFFICIAL if body.get("official") else EvalSuiteRun.RunType.ONE_OFF
        if isinstance(run_type_raw, str):
            run_type_raw = run_type_raw.lower()
        if run_type_raw not in dict(EvalSuiteRun.RunType.choices):
            return HttpResponseBadRequest("Invalid run_type")

        suite = get_object_or_404(
            EvalSuiteRun.objects.prefetch_related("runs__tasks"),
            pk=suite_run_id,
        )

        if suite.run_type != run_type_raw:
            suite.run_type = run_type_raw
            suite.save(update_fields=["run_type", "updated_at"])
            now = timezone.now()
            EvalRun.objects.filter(suite_run_id=suite.id).update(run_type=run_type_raw, updated_at=now)

        suite = EvalSuiteRun.objects.prefetch_related("runs__tasks").get(pk=suite_run_id)

        broadcast_suite_update(suite, include_runs=True)
        for run in suite.runs.all():
            broadcast_run_update(run, include_tasks=True)

        return JsonResponse({"suite_run": _serialize_suite_run(suite, include_runs=True, include_tasks=True)})


@method_decorator(csrf_exempt, name="dispatch")
class EvalRunDetailAPIView(SystemAdminAPIView):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, run_id: str, *args: Any, **kwargs: Any):
        run = get_object_or_404(
            EvalRun.objects.prefetch_related("tasks"),
            pk=run_id,
        )
        payload = _serialize_eval_run(run, include_tasks=True)

        # Add comparison metadata if fingerprint exists
        if run.scenario_fingerprint:
            comparable_count = EvalRun.objects.filter(
                scenario_fingerprint=run.scenario_fingerprint,
                status=EvalRun.Status.COMPLETED,
            ).exclude(id=run.id).count()
            payload["comparison"] = {
                "comparable_runs_count": comparable_count,
                "has_comparable_runs": comparable_count > 0,
            }

        return JsonResponse({"run": payload})


@method_decorator(csrf_exempt, name="dispatch")
class EvalRunCompareAPIView(SystemAdminAPIView):
    """
    Get runs comparable to a given run.

    Supports three comparison tiers via ?tier= parameter:
    - strict: Same fingerprint + same LLM profile lineage (most rigorous)
    - pragmatic (default): Same fingerprint, any config
    - historical: Same scenario slug, any fingerprint (loosest)

    Supports grouping via ?group_by= parameter:
    - code_version: Group by git commit (isolate code changes)
    - primary_model: Group by LLM model (compare models)
    - llm_profile: Group by routing profile (compare configs)

    Additional filters to hold variables constant:
    - ?code_version=: Filter to specific git commit
    - ?primary_model=: Filter to specific model
    """
    http_method_names = ["get"]

    def get(self, request: HttpRequest, run_id: str, *args: Any, **kwargs: Any):
        from django.db.models import Avg, Count, Sum

        run = get_object_or_404(EvalRun, pk=run_id)

        tier = request.GET.get("tier", "pragmatic").lower()
        if tier not in ("strict", "pragmatic", "historical"):
            return HttpResponseBadRequest("tier must be one of: strict, pragmatic, historical")

        group_by = request.GET.get("group_by")
        if group_by and group_by not in ("code_version", "primary_model", "llm_profile"):
            return HttpResponseBadRequest("group_by must be one of: code_version, primary_model, llm_profile")

        run_type_filter = request.GET.get("run_type")
        if run_type_filter:
            run_type_filter = run_type_filter.lower()
            if run_type_filter not in dict(EvalRun.RunType.choices):
                return HttpResponseBadRequest("Invalid run_type")

        # Additional filters to hold variables constant
        code_version_filter = request.GET.get("code_version")
        primary_model_filter = request.GET.get("primary_model")

        limit_raw = request.GET.get("limit", "50")
        try:
            limit = max(1, min(100, int(limit_raw)))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")

        # Build query based on tier
        qs = EvalRun.objects.filter(status=EvalRun.Status.COMPLETED)

        if tier == "strict":
            # Same fingerprint + same LLM profile lineage
            if not run.scenario_fingerprint:
                return JsonResponse({
                    "runs": [],
                    "groups": [],
                    "tier": tier,
                    "target_run_id": str(run.id),
                    "warning": "Target run has no fingerprint - cannot do strict comparison",
                })
            qs = qs.filter(scenario_fingerprint=run.scenario_fingerprint)
            # Filter by LLM profile lineage if the run has one
            if run.llm_routing_profile_id:
                profile = run.llm_routing_profile
                source_id = profile.cloned_from_id if profile.cloned_from_id else profile.id
                qs = qs.filter(
                    models.Q(llm_routing_profile_id=source_id) |
                    models.Q(llm_routing_profile__cloned_from_id=source_id)
                )
        elif tier == "pragmatic":
            # Same fingerprint, any config
            if not run.scenario_fingerprint:
                return JsonResponse({
                    "runs": [],
                    "groups": [],
                    "tier": tier,
                    "target_run_id": str(run.id),
                    "warning": "Target run has no fingerprint - falling back to slug matching",
                })
            qs = qs.filter(scenario_fingerprint=run.scenario_fingerprint)
        else:  # historical
            # Same scenario slug, any fingerprint
            qs = qs.filter(scenario_slug=run.scenario_slug)

        # Apply additional filters
        if run_type_filter:
            qs = qs.filter(run_type=run_type_filter)
        if code_version_filter:
            qs = qs.filter(code_version=code_version_filter)
        if primary_model_filter:
            qs = qs.filter(primary_model=primary_model_filter)

        # Check for fingerprint mismatches in historical tier
        fingerprint_warning = None
        if tier == "historical" and run.scenario_fingerprint:
            mismatched_count = qs.exclude(scenario_fingerprint=run.scenario_fingerprint).count()
            if mismatched_count:
                fingerprint_warning = f"{mismatched_count} run(s) have different fingerprints - eval code may have changed"

        # Handle grouping
        if group_by:
            group_field = {
                "code_version": "code_version",
                "primary_model": "primary_model",
                "llm_profile": "llm_routing_profile_name",
            }[group_by]

            groups = (
                qs.values(group_field)
                .annotate(
                    run_count=Count("id"),
                    avg_cost=Avg("total_cost"),
                    avg_tokens=Avg("tokens_used"),
                    total_tasks=Sum("step_count"),
                    # Pass rate requires counting tasks - simplified here
                )
                .order_by("-run_count")[:limit]
            )

            # Enrich with pass rate by fetching task stats
            groups_list = []
            for g in groups:
                group_value = g[group_field]
                group_runs = qs.filter(**{group_field: group_value}).prefetch_related("tasks")

                # Calculate pass rate across all runs in group
                total_passed = 0
                total_tasks = 0
                for gr in group_runs:
                    for task in gr.tasks.all():
                        total_tasks += 1
                        if task.status == "passed":
                            total_passed += 1

                groups_list.append({
                    "group_by": group_by,
                    "value": group_value or "(none)",
                    "run_count": g["run_count"],
                    "avg_cost": float(g["avg_cost"]) if g["avg_cost"] else 0,
                    "avg_tokens": float(g["avg_tokens"]) if g["avg_tokens"] else 0,
                    "pass_rate": (total_passed / total_tasks * 100) if total_tasks > 0 else 0,
                    "total_tasks": total_tasks,
                    "passed_tasks": total_passed,
                    "is_current": group_value == getattr(run, group_field),
                })

            return JsonResponse({
                "groups": groups_list,
                "group_by": group_by,
                "tier": tier,
                "target_run_id": str(run.id),
                "target_fingerprint": run.scenario_fingerprint or None,
                "fingerprint_warning": fingerprint_warning,
                "filters": {
                    "code_version": code_version_filter,
                    "primary_model": primary_model_filter,
                    "run_type": run_type_filter,
                },
            })

        # Non-grouped: return individual runs (excluding current run)
        runs = list(qs.exclude(id=run.id).order_by("-finished_at")[:limit].prefetch_related("tasks"))

        return JsonResponse({
            "runs": [_serialize_eval_run(r, include_tasks=False) for r in runs],
            "tier": tier,
            "target_run_id": str(run.id),
            "target_fingerprint": run.scenario_fingerprint or None,
            "fingerprint_warning": fingerprint_warning,
        })


@method_decorator(csrf_exempt, name="dispatch")
class EvalSuiteRunCompareAPIView(SystemAdminAPIView):
    """
    Compare suite runs at the aggregate level (across all scenarios).

    Supports three comparison tiers via ?tier= parameter:
    - strict: Same suite + all scenario fingerprints must match
    - pragmatic (default): Same suite + same scenario slugs
    - historical: Same suite slug only (loosest)

    Supports grouping via ?group_by= parameter:
    - code_version: Group by git commit (isolate code changes)
    - primary_model: Group by primary LLM model (compare models)
    - llm_profile: Group by routing profile (compare configs)
    """
    http_method_names = ["get"]

    def get(self, request: HttpRequest, suite_run_id: str, *args: Any, **kwargs: Any):
        suite_run = get_object_or_404(
            EvalSuiteRun.objects.prefetch_related("runs__tasks"),
            pk=suite_run_id,
        )

        tier = request.GET.get("tier", "pragmatic").lower()
        if tier not in ("strict", "pragmatic", "historical"):
            return HttpResponseBadRequest("tier must be one of: strict, pragmatic, historical")

        group_by = request.GET.get("group_by")
        if group_by and group_by not in ("code_version", "primary_model", "llm_profile"):
            return HttpResponseBadRequest("group_by must be one of: code_version, primary_model, llm_profile")

        run_type_filter = request.GET.get("run_type")
        if run_type_filter:
            run_type_filter = run_type_filter.lower()
            if run_type_filter not in dict(EvalSuiteRun.RunType.choices):
                return HttpResponseBadRequest("Invalid run_type")

        limit_raw = request.GET.get("limit", "50")
        try:
            limit = max(1, min(100, int(limit_raw)))
        except ValueError:
            return HttpResponseBadRequest("limit must be an integer")

        # Get fingerprints and scenario slugs from target suite
        target_runs = list(suite_run.runs.all())
        target_fingerprints = {r.scenario_fingerprint for r in target_runs if r.scenario_fingerprint}
        target_scenario_slugs = {r.scenario_slug for r in target_runs}

        # Get primary model from first run (for "is_current" detection)
        target_primary_model = target_runs[0].primary_model if target_runs else None
        target_code_version = target_runs[0].code_version if target_runs else None
        target_llm_profile = target_runs[0].llm_routing_profile_name if target_runs else None

        # Build query for comparable suite runs
        qs = EvalSuiteRun.objects.filter(
            suite_slug=suite_run.suite_slug,
            status=EvalSuiteRun.Status.COMPLETED,
        ).prefetch_related("runs__tasks")

        if tier == "strict":
            # Same suite + all scenario fingerprints must match
            # Filter to suites that have runs with ALL the same fingerprints
            if not target_fingerprints:
                return JsonResponse({
                    "suite_runs": [],
                    "groups": [],
                    "tier": tier,
                    "target_suite_run_id": str(suite_run.id),
                    "warning": "Target suite has no fingerprints - cannot do strict comparison",
                })
            # We'll filter after fetching since this requires checking all runs
        elif tier == "pragmatic":
            # Same suite + same scenario slugs (fingerprints may differ)
            pass  # We'll filter after fetching
        # historical: just same suite_slug, already filtered

        if run_type_filter:
            qs = qs.filter(run_type=run_type_filter)

        # Fetch all candidate suites
        candidate_suites = list(qs.order_by("-finished_at")[:limit * 3])  # Fetch extra for filtering

        # Filter based on tier
        comparable_suites = []
        fingerprint_warning = None
        mismatched_count = 0

        for candidate in candidate_suites:
            candidate_runs = list(candidate.runs.all())
            candidate_fingerprints = {r.scenario_fingerprint for r in candidate_runs if r.scenario_fingerprint}
            candidate_slugs = {r.scenario_slug for r in candidate_runs}

            if tier == "strict":
                # All fingerprints must match exactly
                if candidate_fingerprints == target_fingerprints:
                    comparable_suites.append(candidate)
                elif candidate_slugs == target_scenario_slugs:
                    mismatched_count += 1
            elif tier == "pragmatic":
                # Same scenario slugs required
                if candidate_slugs == target_scenario_slugs:
                    comparable_suites.append(candidate)
                    if candidate_fingerprints != target_fingerprints:
                        mismatched_count += 1
            else:  # historical
                # Any suite with same suite_slug
                comparable_suites.append(candidate)
                if candidate_fingerprints != target_fingerprints:
                    mismatched_count += 1

            if len(comparable_suites) >= limit:
                break

        if mismatched_count > 0 and tier in ("pragmatic", "historical"):
            fingerprint_warning = f"{mismatched_count} suite(s) have different scenario fingerprints - eval code may have changed"

        # Helper to calculate suite stats
        def calc_suite_stats(suite: EvalSuiteRun) -> dict:
            runs = list(suite.runs.all())
            total_passed = 0
            total_tasks = 0
            total_cost = 0.0
            total_tokens = 0

            for run in runs:
                total_cost += float(run.total_cost or 0)
                total_tokens += run.tokens_used or 0
                for task in run.tasks.all():
                    total_tasks += 1
                    if task.status == "passed":
                        total_passed += 1

            return {
                "passed": total_passed,
                "total": total_tasks,
                "pass_rate": (total_passed / total_tasks * 100) if total_tasks > 0 else 0,
                "total_cost": total_cost,
                "total_tokens": total_tokens,
                "primary_model": runs[0].primary_model if runs else None,
                "code_version": runs[0].code_version if runs else None,
                "llm_profile": runs[0].llm_routing_profile_name if runs else None,
            }

        # Handle grouping
        if group_by:
            # Group comparable suites by the specified field
            groups_map: dict[str, list] = {}
            for suite in comparable_suites:
                stats = calc_suite_stats(suite)
                if group_by == "code_version":
                    key = stats["code_version"] or "(none)"
                elif group_by == "primary_model":
                    key = stats["primary_model"] or "(none)"
                else:  # llm_profile
                    key = stats["llm_profile"] or "(none)"

                if key not in groups_map:
                    groups_map[key] = []
                groups_map[key].append({
                    "suite": suite,
                    "stats": stats,
                })

            # Aggregate stats per group
            groups_list = []
            for key, items in groups_map.items():
                total_passed = sum(i["stats"]["passed"] for i in items)
                total_tasks = sum(i["stats"]["total"] for i in items)
                total_cost = sum(i["stats"]["total_cost"] for i in items)
                total_tokens = sum(i["stats"]["total_tokens"] for i in items)
                suite_count = len(items)

                # Determine if this is the current group
                if group_by == "code_version":
                    is_current = key == (target_code_version or "(none)")
                elif group_by == "primary_model":
                    is_current = key == (target_primary_model or "(none)")
                else:
                    is_current = key == (target_llm_profile or "(none)")

                groups_list.append({
                    "group_by": group_by,
                    "value": key,
                    "suite_count": suite_count,
                    "run_count": suite_count,  # For compatibility with frontend
                    "avg_cost": total_cost / suite_count if suite_count > 0 else 0,
                    "avg_tokens": total_tokens / suite_count if suite_count > 0 else 0,
                    "pass_rate": (total_passed / total_tasks * 100) if total_tasks > 0 else 0,
                    "total_tasks": total_tasks,
                    "passed_tasks": total_passed,
                    "is_current": is_current,
                })

            # Sort by pass rate descending
            groups_list.sort(key=lambda x: x["pass_rate"], reverse=True)

            return JsonResponse({
                "groups": groups_list,
                "group_by": group_by,
                "tier": tier,
                "target_suite_run_id": str(suite_run.id),
                "fingerprint_warning": fingerprint_warning,
                "filters": {
                    "run_type": run_type_filter,
                },
            })

        # Non-grouped: return individual suite runs
        suite_runs_data = []
        for suite in comparable_suites:
            if suite.id == suite_run.id:
                continue  # Exclude current suite
            stats = calc_suite_stats(suite)
            suite_runs_data.append({
                "id": str(suite.id),
                "suite_slug": suite.suite_slug,
                "status": suite.status,
                "run_type": suite.run_type,
                "started_at": suite.started_at.isoformat() if suite.started_at else None,
                "finished_at": suite.finished_at.isoformat() if suite.finished_at else None,
                "code_version": stats["code_version"],
                "primary_model": stats["primary_model"],
                "llm_profile": stats["llm_profile"],
                "pass_rate": stats["pass_rate"],
                "total_cost": stats["total_cost"],
                "total_tokens": stats["total_tokens"],
                "passed_tasks": stats["passed"],
                "total_tasks": stats["total"],
            })

        return JsonResponse({
            "suite_runs": suite_runs_data,
            "tier": tier,
            "target_suite_run_id": str(suite_run.id),
            "fingerprint_warning": fingerprint_warning,
        })
