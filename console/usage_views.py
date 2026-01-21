from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDay, TruncHour
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views import View

from billing.services import BillingService
from tasks.services import TaskCreditService

from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    Organization,
    PersistentAgentStep,
    PersistentAgentToolCall,
)
from console.context_helpers import build_console_context
from util.constants.task_constants import TASKS_UNLIMITED
from util.subscription_helper import allow_organization_extra_tasks, allow_user_extra_tasks


API_AGENT_ID = "api"
API_AGENT_NAME = "API"
API_CREDIT_DECIMAL = Decimal("1")
DECIMAL_ZERO = Decimal("0")
EVAL_ENVIRONMENT = "eval"


@dataclass(frozen=True)
class UsageAgentDescriptor:
    id: str
    name: str
    browser_agent_id: Optional[uuid.UUID]
    persistent_agent_id: Optional[uuid.UUID] = None
    is_api: bool = False


def _parse_query_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_period_label(start_date: date, end_date: date) -> str:
    """Return a concise date range label such as 'Jul 1 – Jul 31, 2024'."""

    start_month = start_date.strftime("%b")
    end_month = end_date.strftime("%b")

    if start_date.year == end_date.year:
        start_label = f"{start_month} {start_date.day}"
        end_label = f"{end_month} {end_date.day}, {end_date.year}"
    else:
        start_label = f"{start_month} {start_date.day}, {start_date.year}"
        end_label = f"{end_month} {end_date.day}, {end_date.year}"

    return f"{start_label} – {end_label}"


def _exclude_eval_browser_tasks(qs):
    return qs.exclude(agent__persistent_agent__execution_environment=EVAL_ENVIRONMENT)


def _exclude_eval_persistent_steps(qs):
    return qs.exclude(agent__execution_environment=EVAL_ENVIRONMENT)


def _exclude_eval_tool_calls(qs):
    return qs.exclude(step__agent__execution_environment=EVAL_ENVIRONMENT)


def _get_accessible_agents(request: HttpRequest, organization: Organization | None) -> list[UsageAgentDescriptor]:
    if organization is not None:
        qs = BrowserUseAgent.objects.filter(
            Q(persistent_agent__organization=organization)
        )
    else:
        qs = BrowserUseAgent.objects.filter(user=request.user).filter(
            Q(persistent_agent__organization__isnull=True) | Q(persistent_agent__isnull=True)
        )
    qs = qs.exclude(persistent_agent__execution_environment=EVAL_ENVIRONMENT)

    descriptors: list[UsageAgentDescriptor] = [
        UsageAgentDescriptor(
            id=API_AGENT_ID,
            name=API_AGENT_NAME,
            browser_agent_id=None,
            persistent_agent_id=None,
            is_api=True,
        )
    ]

    for agent in qs.select_related("persistent_agent").order_by("name"):
        persistent_obj = getattr(agent, "persistent_agent", None)
        persistent_agent_id = getattr(persistent_obj, "id", None)
        descriptors.append(
            UsageAgentDescriptor(
                id=str(agent.id),
                name=agent.name,
                browser_agent_id=agent.id,
                persistent_agent_id=persistent_agent_id,
            )
        )

    # Sort alphabetically so the API option appears in a predictable position.
    descriptors.sort(key=lambda descriptor: descriptor.name.lower())
    return descriptors


def _filter_agent_ids(raw_values: Iterable[str], accessible_ids: set[str]) -> list[str]:
    filtered: list[str] = []
    for raw in raw_values:
        if raw in accessible_ids:
            filtered.append(raw)
    return filtered


def _split_agent_filter_values(agent_ids: Iterable[str]) -> tuple[list[uuid.UUID], bool]:
    concrete_ids: list[uuid.UUID] = []
    include_api = False
    for agent_id in agent_ids:
        if agent_id == API_AGENT_ID:
            include_api = True
            continue
        try:
            concrete_ids.append(uuid.UUID(agent_id))
        except (TypeError, ValueError):
            continue
    return concrete_ids, include_api


def _build_agent_filter(actual_agent_ids: Iterable[uuid.UUID], include_api: bool) -> Optional[Q]:
    clauses: list[Q] = []
    actual_ids = list(actual_agent_ids)
    if actual_ids:
        clauses.append(Q(agent_id__in=actual_ids))
    if include_api:
        clauses.append(Q(agent_id__isnull=True))

    if not clauses:
        return None

    combined = clauses[0]
    for clause in clauses[1:]:
        combined |= clause
    return combined


def _per_task_credit_expression() -> Case:
    zero_decimal = Value(Decimal("0"), output_field=DecimalField(max_digits=20, decimal_places=6))
    return Case(
        When(
            agent_id__isnull=True,
            then=Coalesce(F("credits_cost"), Value(API_CREDIT_DECIMAL, output_field=DecimalField(max_digits=20, decimal_places=6))),
        ),
        default=Coalesce(F("credits_cost"), zero_decimal),
        output_field=DecimalField(max_digits=20, decimal_places=6),
    )


def _resolve_agent_selection(
        agent_filters_raw: Iterable[str],
        accessible_agents: list[UsageAgentDescriptor],
) -> tuple[list[str], list[uuid.UUID], bool, list[UsageAgentDescriptor], list[uuid.UUID]]:
    accessible_map = {agent.id: agent for agent in accessible_agents}
    accessible_ids = set(accessible_map.keys())
    filtered_agent_ids = _filter_agent_ids(agent_filters_raw, accessible_ids)
    actual_agent_ids, include_api = _split_agent_filter_values(filtered_agent_ids)

    if filtered_agent_ids:
        selected_agents = [accessible_map[agent_id] for agent_id in filtered_agent_ids]
    else:
        selected_agents = accessible_agents

    persistent_agent_ids = [
        agent.persistent_agent_id for agent in selected_agents if agent.persistent_agent_id is not None
    ]

    return filtered_agent_ids, actual_agent_ids, include_api, selected_agents, persistent_agent_ids


class UsageSummaryAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        resolved = build_console_context(request)

        owner = request.user
        owner_context_type = "personal"
        organization = None

        if resolved.current_context.type == "organization" and resolved.current_membership:
            organization = resolved.current_membership.org
            owner = organization
            owner_context_type = "organization"

        requested_start = _parse_query_date(request.GET.get("from"))
        requested_end = _parse_query_date(request.GET.get("to"))
        agent_filters_raw = request.GET.getlist("agent")

        accessible_agents = _get_accessible_agents(request, organization)

        if requested_start and requested_end and requested_start <= requested_end:
            period_start, period_end = requested_start, requested_end
        else:
            period_start, period_end = BillingService.get_current_billing_period_for_owner(owner)

        tz = timezone.get_current_timezone()
        period_start_dt = timezone.make_aware(datetime.combine(period_start, time.min), tz)
        period_end_dt = timezone.make_aware(datetime.combine(period_end, time.max), tz)

        filters = {
            "is_deleted": False,
            "created_at__gte": period_start_dt,
            "created_at__lte": period_end_dt,
        }

        if organization is not None:
            filters["organization"] = organization
        else:
            filters["user"] = request.user
            filters["organization__isnull"] = True

        (
            filtered_agent_ids,
            actual_agent_ids,
            include_api,
            _selected_agents,
            persistent_agent_ids,
        ) = _resolve_agent_selection(agent_filters_raw, accessible_agents)

        tasks_qs = _exclude_eval_browser_tasks(BrowserUseAgentTask.objects.filter(**filters))
        agent_filter_q = _build_agent_filter(actual_agent_ids, include_api)
        if agent_filter_q is not None:
            tasks_qs = tasks_qs.filter(agent_filter_q)

        zero_value = Value(DECIMAL_ZERO, output_field=DecimalField(max_digits=20, decimal_places=6))
        status_credit_totals: dict[str, Decimal] = {
            status: DECIMAL_ZERO for status in BrowserUseAgentTask.StatusChoices.values
        }
        credit_annotation = Coalesce(Sum(_per_task_credit_expression()), zero_value)
        for row in tasks_qs.values("status").annotate(total=credit_annotation):
            status = row.get("status")
            if status is None:
                continue
            status_credit_totals[status] = row.get("total") or DECIMAL_ZERO

        task_credit_total = sum(status_credit_totals.values(), DECIMAL_ZERO)

        persistent_filters = {
            "created_at__gte": period_start_dt,
            "created_at__lte": period_end_dt,
        }
        if organization is not None:
            persistent_filters["agent__organization"] = organization
        else:
            persistent_filters["agent__user"] = request.user
            persistent_filters["agent__organization__isnull"] = True

        persistent_steps_qs = _exclude_eval_persistent_steps(PersistentAgentStep.objects.filter(**persistent_filters))
        if persistent_agent_ids:
            persistent_steps_qs = persistent_steps_qs.filter(agent_id__in=persistent_agent_ids)
        elif filtered_agent_ids:
            persistent_steps_qs = PersistentAgentStep.objects.none()

        persistent_credit_agg = persistent_steps_qs.aggregate(
            total=Coalesce(Sum("credits_cost"), zero_value),
        )
        persistent_credit_total = persistent_credit_agg.get("total") or DECIMAL_ZERO

        combined_total = task_credit_total + persistent_credit_total
        completed_credit = status_credit_totals.get(BrowserUseAgentTask.StatusChoices.COMPLETED, DECIMAL_ZERO)
        combined_completed = completed_credit + persistent_credit_total
        in_progress_credit = status_credit_totals.get(BrowserUseAgentTask.StatusChoices.IN_PROGRESS, DECIMAL_ZERO)
        pending_credit = status_credit_totals.get(BrowserUseAgentTask.StatusChoices.PENDING, DECIMAL_ZERO)
        failed_credit = status_credit_totals.get(BrowserUseAgentTask.StatusChoices.FAILED, DECIMAL_ZERO)
        cancelled_credit = status_credit_totals.get(BrowserUseAgentTask.StatusChoices.CANCELLED, DECIMAL_ZERO)
        total_credits = combined_total

        credits_zero = Value(Decimal("0"), output_field=DecimalField(max_digits=20, decimal_places=6))

        task_credit_qs = TaskCreditService.get_current_task_credit_for_owner(owner)
        credit_agg = task_credit_qs.aggregate(
            available=Coalesce(Sum("available_credits"), credits_zero),
            total=Coalesce(Sum("credits"), credits_zero),
            used=Coalesce(Sum("credits_used"), credits_zero),
        )

        def _to_decimal(value: object, default: Decimal = DECIMAL_ZERO) -> Decimal:
            try:
                return Decimal(value)
            except (TypeError, ValueError, InvalidOperation):
                return default

        ledger_available = _to_decimal(credit_agg.get("available"), DECIMAL_ZERO)
        ledger_total = _to_decimal(credit_agg.get("total"), DECIMAL_ZERO)
        ledger_used = _to_decimal(credit_agg.get("used"), DECIMAL_ZERO)

        if organization is None:
            quota_total = _to_decimal(TaskCreditService.get_tasks_entitled_for_owner(owner), DECIMAL_ZERO)
            quota_used = _to_decimal(
                TaskCreditService.get_owner_task_credits_used(owner, task_credits=task_credit_qs),
                DECIMAL_ZERO,
            )
            available_credits = _to_decimal(
                TaskCreditService.calculate_available_tasks_for_owner(owner, task_credits=task_credit_qs),
                DECIMAL_ZERO,
            )
            extra_tasks_enabled = allow_user_extra_tasks(request.user)
        else:
            quota_total = ledger_total
            quota_used = ledger_used
            available_credits = ledger_available
            extra_tasks_enabled = allow_organization_extra_tasks(organization)

        unlimited_quota = quota_total == Decimal(TASKS_UNLIMITED)
        if not unlimited_quota:
            available_credits = max(available_credits, DECIMAL_ZERO)

        quota_used_pct = 0.0
        if not unlimited_quota and quota_total > 0:
            usage_pct = (quota_used / quota_total) * Decimal("100")
            quota_used_pct = float(min(usage_pct, Decimal("100")))

        payload = {
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
                "label": _format_period_label(period_start, period_end),
                "timezone": timezone.get_current_timezone_name(),
            },
            "context": {
                "type": owner_context_type,
                "id": resolved.current_context.id,
                "name": resolved.current_context.name,
            },
            "metrics": {
                "tasks": {
                    "count": float(combined_total),
                    "completed": float(combined_completed),
                    "in_progress": float(in_progress_credit),
                    "pending": float(pending_credit),
                    "failed": float(failed_credit),
                    "cancelled": float(cancelled_credit),
                },
                "credits": {
                    "total": float(total_credits),
                    "unit": "credits",
                },
                "quota": {
                    "available": float(available_credits),
                    "total": float(quota_total),
                    "used": float(quota_used),
                    "used_pct": quota_used_pct,
                },
            },
            "extra_tasks": {
                "enabled": extra_tasks_enabled,
            },
        }

        return JsonResponse(payload)


class UsageTrendAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        resolved = build_console_context(request)

        organization = None

        if resolved.current_context.type == "organization" and resolved.current_membership:
            organization = resolved.current_membership.org

        requested_start = _parse_query_date(request.GET.get("from"))
        requested_end = _parse_query_date(request.GET.get("to"))
        mode = request.GET.get("mode", "week")
        agent_filters_raw = request.GET.getlist("agent")

        if mode not in {"day", "week", "month"}:
            return JsonResponse({"error": "Invalid mode."}, status=400)

        tz = timezone.get_current_timezone()
        tz_name = timezone.get_current_timezone_name()

        accessible_agents = _get_accessible_agents(request, organization)

        anchor_end_date = requested_end or timezone.now().date()
        if requested_start and anchor_end_date < requested_start:
            anchor_end_date = requested_start

        if mode == "day":
            if requested_start:
                current_start_date = requested_start
            else:
                current_start_date = anchor_end_date

            if requested_end:
                current_end_date = requested_end
            else:
                current_end_date = current_start_date

            if current_end_date < current_start_date:
                current_end_date = current_start_date

            step = timedelta(hours=1)
            current_start_dt = timezone.make_aware(datetime.combine(current_start_date, time.min), tz)
            current_end_dt = timezone.make_aware(
                datetime.combine(current_end_date + timedelta(days=1), time.min), tz
            )
        else:
            if requested_start:
                current_start_date = requested_start
            else:
                lookback_days = 6 if mode == "week" else 29
                current_start_date = anchor_end_date - timedelta(days=lookback_days)

            current_end_date = requested_end or anchor_end_date

            if current_end_date < current_start_date:
                current_end_date = current_start_date

            step = timedelta(days=1)
            current_start_dt = timezone.make_aware(datetime.combine(current_start_date, time.min), tz)
            current_end_dt = timezone.make_aware(
                datetime.combine(current_end_date + timedelta(days=1), time.min), tz
            )

        current_duration = current_end_dt - current_start_dt
        previous_end_dt = current_start_dt
        previous_start_dt = previous_end_dt - current_duration

        base_filters = {
            "is_deleted": False,
        }

        if organization is not None:
            base_filters["organization"] = organization
        else:
            base_filters["user"] = request.user
            base_filters["organization__isnull"] = True

        persistent_base_filters: dict[str, object] = {}
        if organization is not None:
            persistent_base_filters["agent__organization"] = organization
        else:
            persistent_base_filters["agent__user"] = request.user
            persistent_base_filters["agent__organization__isnull"] = True

        (
            filtered_agent_ids,
            actual_agent_ids,
            include_api,
            active_agents,
            persistent_agent_ids,
        ) = _resolve_agent_selection(agent_filters_raw, accessible_agents)

        agent_filter_q = _build_agent_filter(actual_agent_ids, include_api)

        trunc_function = TruncHour if step == timedelta(hours=1) else TruncDay
        persistent_id_map = {
            agent.persistent_agent_id: agent.id
            for agent in accessible_agents
            if agent.persistent_agent_id is not None
        }

        zero_value = Value(DECIMAL_ZERO, output_field=DecimalField(max_digits=20, decimal_places=6))

        def _build_counts(start_dt: datetime, end_dt: datetime) -> dict[str, float]:
            filters = base_filters | {
                "created_at__gte": start_dt,
                "created_at__lt": end_dt,
            }
            qs = _exclude_eval_browser_tasks(BrowserUseAgentTask.objects.filter(**filters))
            if agent_filter_q is not None:
                qs = qs.filter(agent_filter_q)
            rows = (
                qs.annotate(bucket=trunc_function("created_at", tzinfo=tz))
                .values("bucket")
                .order_by("bucket")
                .annotate(total=Coalesce(Sum(_per_task_credit_expression()), zero_value))
            )
            counts: dict[str, float] = {}
            for row in rows:
                bucket = row.get("bucket")
                if bucket is None:
                    continue
                bucket_key = bucket.isoformat()
                counts[bucket_key] = float(row.get("total") or DECIMAL_ZERO)

            persistent_filters = persistent_base_filters | {
                "created_at__gte": start_dt,
                "created_at__lt": end_dt,
            }

            steps_qs = _exclude_eval_persistent_steps(PersistentAgentStep.objects.filter(**persistent_filters))
            if persistent_agent_ids:
                steps_qs = steps_qs.filter(agent_id__in=persistent_agent_ids)
            elif filtered_agent_ids:
                steps_qs = PersistentAgentStep.objects.none()

            step_rows = (
                steps_qs.annotate(bucket=trunc_function("created_at", tzinfo=tz))
                .values("bucket")
                .order_by("bucket")
                .annotate(total=Coalesce(Sum("credits_cost"), zero_value))
            )
            for row in step_rows:
                bucket = row.get("bucket")
                if bucket is None:
                    continue
                bucket_key = bucket.isoformat()
                counts[bucket_key] = counts.get(bucket_key, 0.0) + float(row.get("total") or DECIMAL_ZERO)

            return counts

        def _build_agent_counts(start_dt: datetime, end_dt: datetime) -> dict[str, dict[str, float]]:
            filters = base_filters | {
                "created_at__gte": start_dt,
                "created_at__lt": end_dt,
            }
            qs = _exclude_eval_browser_tasks(BrowserUseAgentTask.objects.filter(**filters))
            if agent_filter_q is not None:
                qs = qs.filter(agent_filter_q)
            rows = (
                qs.annotate(bucket=trunc_function("created_at", tzinfo=tz))
                .values("bucket", "agent_id")
                .order_by("bucket", "agent_id")
                .annotate(total=Coalesce(Sum(_per_task_credit_expression()), zero_value))
            )
            bucket_map: dict[str, dict[str, float]] = {}
            for row in rows:
                bucket = row.get("bucket")
                if bucket is None:
                    continue
                agent_id = row.get("agent_id")
                agent_key = API_AGENT_ID if agent_id is None else str(agent_id)
                bucket_key = bucket.isoformat()
                agent_counts = bucket_map.setdefault(bucket_key, {})
                agent_counts[agent_key] = float(row.get("total") or DECIMAL_ZERO)

            persistent_filters = persistent_base_filters | {
                "created_at__gte": start_dt,
                "created_at__lt": end_dt,
            }

            steps_qs = _exclude_eval_persistent_steps(PersistentAgentStep.objects.filter(**persistent_filters))
            if persistent_agent_ids:
                steps_qs = steps_qs.filter(agent_id__in=persistent_agent_ids)
            elif filtered_agent_ids:
                steps_qs = PersistentAgentStep.objects.none()

            step_rows = (
                steps_qs.annotate(bucket=trunc_function("created_at", tzinfo=tz))
                .values("bucket", "agent_id")
                .order_by("bucket", "agent_id")
                .annotate(total=Coalesce(Sum("credits_cost"), zero_value))
            )
            for row in step_rows:
                bucket = row.get("bucket")
                if bucket is None:
                    continue
                persistent_agent_id = row.get("agent_id")
                browser_agent_id = persistent_id_map.get(persistent_agent_id)
                if browser_agent_id is None:
                    continue
                bucket_key = bucket.isoformat()
                agent_counts = bucket_map.setdefault(bucket_key, {})
                agent_counts[browser_agent_id] = agent_counts.get(browser_agent_id, 0.0) + float(row.get("total") or DECIMAL_ZERO)

            return bucket_map

        current_counts = _build_counts(current_start_dt, current_end_dt)
        current_agent_counts = _build_agent_counts(current_start_dt, current_end_dt)
        previous_counts = _build_counts(previous_start_dt, previous_end_dt)

        buckets: list[dict[str, object]] = []
        current_cursor = current_start_dt
        previous_cursor = previous_start_dt
        while current_cursor < current_end_dt:
            current_key = current_cursor.isoformat()
            previous_key = previous_cursor.isoformat()
            agent_counts = current_agent_counts.get(current_key, {})
            buckets.append(
                {
                    "timestamp": current_key,
                    "current": current_counts.get(current_key, 0),
                    "previous": previous_counts.get(previous_key, 0),
                    "agents": agent_counts,
                }
            )
            current_cursor += step
            previous_cursor += step

        payload = {
            "mode": mode,
            "resolution": "hour" if step == timedelta(hours=1) else "day",
            "timezone": tz_name,
            "current_period": {
                "start": current_start_dt.isoformat(),
                "end": current_end_dt.isoformat(),
            },
            "previous_period": {
                "start": previous_start_dt.isoformat(),
                "end": previous_end_dt.isoformat(),
            },
            "agents": [
                {
                    "id": str(agent.id),
                    "name": agent.name,
                }
                for agent in active_agents
            ],
            "buckets": buckets,
        }

        return JsonResponse(payload)


class UsageToolBreakdownAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        resolved = build_console_context(request)

        organization = None

        if resolved.current_context.type == "organization" and resolved.current_membership:
            organization = resolved.current_membership.org

        requested_start = _parse_query_date(request.GET.get("from"))
        requested_end = _parse_query_date(request.GET.get("to"))
        agent_filters_raw = request.GET.getlist("agent")

        owner = organization if organization is not None else request.user

        if requested_start and requested_end and requested_start <= requested_end:
            period_start, period_end = requested_start, requested_end
        else:
            period_start, period_end = BillingService.get_current_billing_period_for_owner(owner)

        tz = timezone.get_current_timezone()
        tz_name = timezone.get_current_timezone_name()
        start_dt = timezone.make_aware(datetime.combine(period_start, time.min), tz)
        end_dt = timezone.make_aware(datetime.combine(period_end, time.max), tz)

        accessible_agents = _get_accessible_agents(request, organization)
        accessible_agent_ids = {agent.id for agent in accessible_agents}
        filtered_agent_ids = _filter_agent_ids(agent_filters_raw, accessible_agent_ids)
        actual_agent_ids, include_api = _split_agent_filter_values(filtered_agent_ids)
        agent_filter_q = _build_agent_filter(actual_agent_ids, include_api)

        filters = {
            "step__created_at__gte": start_dt,
            "step__created_at__lte": end_dt,
        }

        if organization is not None:
            filters["step__agent__organization"] = organization
        else:
            filters["step__agent__user"] = request.user
            filters["step__agent__organization__isnull"] = True

        zero_decimal = Value(DECIMAL_ZERO, output_field=DecimalField(max_digits=20, decimal_places=6))

        persistent_qs = _exclude_eval_tool_calls(PersistentAgentToolCall.objects.filter(**filters))
        if filtered_agent_ids:
            if actual_agent_ids:
                persistent_q_filter = Q(step__agent__browser_use_agent_id__in=actual_agent_ids)
                persistent_qs = persistent_qs.filter(persistent_q_filter)
            else:
                # Only API usage was requested; no persistent agent tool calls should be returned.
                persistent_qs = persistent_qs.none()

        tool_rows_query = (
            persistent_qs
            .values("tool_name")
            .annotate(
                invocations=Count("tool_name"),
                credits=Coalesce(Sum("step__credits_cost"), zero_decimal),
            )
            .order_by("-credits", "-invocations")
        )
        tool_rows: list[dict[str, object]] = []
        for row in tool_rows_query:
            tool_rows.append(
                {
                    "tool_name": row.get("tool_name") or "",
                    "invocations": int(row.get("invocations", 0) or 0),
                    "credits": row.get("credits") or DECIMAL_ZERO,
                }
            )

        # Compute total persistent step credits so non-tool work can be surfaced.
        step_filters = {
            "created_at__gte": start_dt,
            "created_at__lte": end_dt,
        }
        if organization is not None:
            step_filters["agent__organization"] = organization
        else:
            step_filters["agent__user"] = request.user
            step_filters["agent__organization__isnull"] = True

        steps_qs = _exclude_eval_persistent_steps(PersistentAgentStep.objects.filter(**step_filters))
        if filtered_agent_ids:
            if actual_agent_ids:
                steps_qs = steps_qs.filter(agent__browser_use_agent_id__in=actual_agent_ids)
            else:
                steps_qs = steps_qs.none()

        persistent_step_totals = steps_qs.aggregate(
            total=Coalesce(Sum("credits_cost"), zero_decimal),
        )
        persistent_step_credits = persistent_step_totals.get("total") or DECIMAL_ZERO

        # Include API-originated browser tasks (agentless) as their own category.
        api_task_filters = {
            "is_deleted": False,
            "created_at__gte": start_dt,
            "created_at__lte": end_dt,
        }

        if organization is not None:
            api_task_filters["organization"] = organization
        else:
            api_task_filters["user"] = request.user
            api_task_filters["organization__isnull"] = True

        api_tasks_qs = BrowserUseAgentTask.objects.filter(**api_task_filters)
        if agent_filter_q is not None:
            api_tasks_qs = api_tasks_qs.filter(agent_filter_q)
        api_tasks_qs = api_tasks_qs.filter(agent_id__isnull=True)

        api_task_stats = api_tasks_qs.aggregate(
            invocations=Count("id"),
            credits=Coalesce(Sum(_per_task_credit_expression()), zero_decimal),
        )

        api_task_invocations = api_task_stats.get("invocations", 0) or 0
        api_task_credits = api_task_stats.get("credits") or DECIMAL_ZERO

        if api_task_credits > DECIMAL_ZERO:
            tool_rows.append(
                {
                    "tool_name": "api_task",
                    "invocations": int(api_task_invocations),
                    "credits": api_task_credits,
                }
            )

        total_tool_credits = sum((row["credits"] or DECIMAL_ZERO) for row in tool_rows)
        total_invocations = sum(int(row.get("invocations", 0) or 0) for row in tool_rows)

        residual_credits = persistent_step_credits - total_tool_credits
        if residual_credits > DECIMAL_ZERO:
            tool_rows.append(
                {
                    "tool_name": "agent_runtime",
                    "invocations": 0,
                    "credits": residual_credits,
                }
            )
            total_tool_credits += residual_credits  # keep totals in sync

        payload = {
            "range": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            },
            "timezone": tz_name,
            "total_count": float(total_tool_credits),
            "total_credits": float(total_tool_credits),
            "total_invocations": total_invocations,
            "tools": [
                {
                    "name": (row["tool_name"] or ""),
                    "invocations": int(row["invocations"]),
                    "credits": float(row["credits"] or DECIMAL_ZERO),
                }
                for row in tool_rows
            ],
        }

        return JsonResponse(payload)


class UsageAgentLeaderboardAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        resolved = build_console_context(request)

        organization = None

        if resolved.current_context.type == "organization" and resolved.current_membership:
            organization = resolved.current_membership.org

        owner = organization if organization is not None else request.user

        requested_start = _parse_query_date(request.GET.get("from"))
        requested_end = _parse_query_date(request.GET.get("to"))
        agent_filters_raw = request.GET.getlist("agent")

        if requested_start and requested_end and requested_start <= requested_end:
            period_start, period_end = requested_start, requested_end
        else:
            period_start, period_end = BillingService.get_current_billing_period_for_owner(owner)

        tz = timezone.get_current_timezone()
        tz_name = timezone.get_current_timezone_name()
        period_start_dt = timezone.make_aware(datetime.combine(period_start, time.min), tz)
        period_end_dt = timezone.make_aware(datetime.combine(period_end, time.max), tz)

        accessible_agents = _get_accessible_agents(request, organization)
        (
            filtered_agent_ids,
            actual_agent_ids,
            include_api,
            active_agents,
            persistent_agent_ids,
        ) = _resolve_agent_selection(agent_filters_raw, accessible_agents)

        task_filters = {
            "is_deleted": False,
            "created_at__gte": period_start_dt,
            "created_at__lte": period_end_dt,
        }

        if organization is not None:
            task_filters["organization"] = organization
        else:
            task_filters["user"] = request.user
            task_filters["organization__isnull"] = True

        agent_filter_q = _build_agent_filter(actual_agent_ids, include_api)

        tasks_qs = _exclude_eval_browser_tasks(BrowserUseAgentTask.objects.filter(**task_filters))
        if agent_filter_q is not None:
            tasks_qs = tasks_qs.filter(agent_filter_q)

        zero_value = Value(DECIMAL_ZERO, output_field=DecimalField(max_digits=20, decimal_places=6))

        aggregates = (
            tasks_qs
            .values("agent_id")
            .order_by()
            .annotate(
                total=Coalesce(Sum(_per_task_credit_expression()), zero_value),
                success=Coalesce(
                    Sum(_per_task_credit_expression(), filter=Q(status=BrowserUseAgentTask.StatusChoices.COMPLETED)),
                    zero_value,
                ),
                error=Coalesce(
                    Sum(_per_task_credit_expression(), filter=Q(status=BrowserUseAgentTask.StatusChoices.FAILED)),
                    zero_value,
                ),
            )
        )

        aggregate_map: dict[str, dict[str, Decimal]] = {}
        for row in aggregates:
            agent_id = row.get("agent_id")
            key = API_AGENT_ID if agent_id is None else str(agent_id)
            aggregate_map[key] = {
                "total": row.get("total") or DECIMAL_ZERO,
                "success": row.get("success") or DECIMAL_ZERO,
                "error": row.get("error") or DECIMAL_ZERO,
            }

        persistent_filters = {
            "created_at__gte": period_start_dt,
            "created_at__lte": period_end_dt,
        }
        if organization is not None:
            persistent_filters["agent__organization"] = organization
        else:
            persistent_filters["agent__user"] = request.user
            persistent_filters["agent__organization__isnull"] = True

        steps_qs = _exclude_eval_persistent_steps(PersistentAgentStep.objects.filter(**persistent_filters))
        if persistent_agent_ids:
            steps_qs = steps_qs.filter(agent_id__in=persistent_agent_ids)
        elif filtered_agent_ids:
            steps_qs = PersistentAgentStep.objects.none()

        persistent_id_map = {
            agent.persistent_agent_id: agent.id
            for agent in accessible_agents
            if agent.persistent_agent_id is not None
        }

        for row in (
                steps_qs
                        .values("agent_id")
                        .order_by()
                        .annotate(total=Coalesce(Sum("credits_cost"), zero_value))
        ):
            persistent_agent_id = row.get("agent_id")
            browser_agent_id = persistent_id_map.get(persistent_agent_id)
            if browser_agent_id is None:
                continue
            total = row.get("total") or DECIMAL_ZERO
            stats = aggregate_map.setdefault(
                browser_agent_id,
                {"total": DECIMAL_ZERO, "success": DECIMAL_ZERO, "error": DECIMAL_ZERO},
            )
            stats["total"] += total
            stats["success"] += total

        period_length_days = max((period_end - period_start).days + 1, 1)

        leaderboard: list[dict[str, object]] = []
        for agent in active_agents:
            stats = aggregate_map.get(agent.id, {"total": DECIMAL_ZERO, "success": DECIMAL_ZERO, "error": DECIMAL_ZERO})
            total = stats["total"]
            success = stats["success"]
            error = stats["error"]
            avg_per_day = float(total) / period_length_days if total > 0 else 0.0

            leaderboard.append(
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "tasks_total": float(total),
                    "tasks_per_day": avg_per_day,
                    "success_count": float(success),
                    "error_count": float(error),
                    "persistent_id": str(agent.persistent_agent_id) if agent.persistent_agent_id else None,
                }
            )

        leaderboard.sort(key=lambda entry: entry["tasks_total"], reverse=True)

        payload = {
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
                "label": _format_period_label(period_start, period_end),
                "timezone": tz_name,
            },
            "agents": leaderboard,
        }

        return JsonResponse(payload)


class UsageAgentsAPIView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        resolved = build_console_context(request)

        organization = None

        if resolved.current_context.type == "organization" and resolved.current_membership:
            organization = resolved.current_membership.org

        accessible_agents = _get_accessible_agents(request, organization)

        agents = [
            {
                "id": agent.id,
                "name": agent.name,
            }
            for agent in accessible_agents
        ]

        return JsonResponse({"agents": agents})
