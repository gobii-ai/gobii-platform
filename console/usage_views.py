from datetime import date, datetime, time, timedelta
import uuid
from decimal import Decimal
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, DecimalField, Sum, Value
from django.db.models import Q
from django.db.models.functions import Coalesce, TruncDay, TruncHour
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views import View

from billing.services import BillingService

from api.models import BrowserUseAgent, BrowserUseAgentTask, PersistentAgentToolCall, TaskCredit
from console.context_helpers import build_console_context


def _parse_query_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_period_label(start_date, end_date) -> str:
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


def _get_accessible_agents(request: HttpRequest, organization):
    if organization is not None:
        qs = BrowserUseAgent.objects.filter(
            Q(persistent_agent__organization=organization)
        )
    else:
        qs = BrowserUseAgent.objects.filter(user=request.user).filter(
            Q(persistent_agent__organization__isnull=True) | Q(persistent_agent__isnull=True)
        )
    return list(qs.order_by("name"))


def _filter_agent_ids(raw_values, accessible_ids: set[uuid.UUID]) -> list[uuid.UUID]:
    filtered: list[uuid.UUID] = []
    for raw in raw_values:
        try:
            candidate = uuid.UUID(raw)
        except (TypeError, ValueError):
            continue
        if candidate in accessible_ids:
            filtered.append(candidate)
    return filtered


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

        accessible_agent_ids = {agent.id for agent in _get_accessible_agents(request, organization)}

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

        filtered_agent_ids = _filter_agent_ids(agent_filters_raw, accessible_agent_ids)
        if filtered_agent_ids:
            filters["agent_id__in"] = filtered_agent_ids

        tasks_qs = BrowserUseAgentTask.objects.filter(**filters)

        status_counts = {status: 0 for status in BrowserUseAgentTask.StatusChoices.values}
        for row in tasks_qs.values("status").annotate(count=Count("status")):
            status_counts[row["status"]] = row["count"]

        tasks_total = tasks_qs.count()

        credits_zero = Value(Decimal("0"), output_field=DecimalField(max_digits=20, decimal_places=6))
        credits_agg = tasks_qs.aggregate(total=Coalesce(Sum("credits_cost"), credits_zero))
        total_credits = credits_agg.get("total") or Decimal("0")

        now = timezone.now()
        credit_filters = {
            "granted_date__lte": now,
            "expiration_date__gte": now,
            "voided": False,
        }
        if organization is not None:
            credit_filters["organization"] = organization
        else:
            credit_filters["user"] = request.user

        credit_agg = TaskCredit.objects.filter(**credit_filters).aggregate(
            available=Coalesce(Sum("available_credits"), credits_zero),
            total=Coalesce(Sum("credits"), credits_zero),
            used=Coalesce(Sum("credits_used"), credits_zero),
        )

        available_credits = credit_agg.get("available") or Decimal("0")
        quota_total = credit_agg.get("total") or Decimal("0")
        quota_used = credit_agg.get("used") or Decimal("0")

        quota_used_pct = 0.0
        if quota_total > 0:
            quota_used_pct = float((quota_used / quota_total) * Decimal("100"))

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
                    "count": tasks_total,
                    "completed": status_counts.get(BrowserUseAgentTask.StatusChoices.COMPLETED, 0),
                    "in_progress": status_counts.get(BrowserUseAgentTask.StatusChoices.IN_PROGRESS, 0),
                    "pending": status_counts.get(BrowserUseAgentTask.StatusChoices.PENDING, 0),
                    "failed": status_counts.get(BrowserUseAgentTask.StatusChoices.FAILED, 0),
                    "cancelled": status_counts.get(BrowserUseAgentTask.StatusChoices.CANCELLED, 0),
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
        accessible_agent_ids = {agent.id for agent in accessible_agents}

        anchor_end_date = requested_end or timezone.now().date()
        if requested_start and anchor_end_date < requested_start:
            anchor_end_date = requested_start

        if mode == "day":
            current_start_date = anchor_end_date
            current_end_date = anchor_end_date
            step = timedelta(hours=1)
            current_start_dt = timezone.make_aware(datetime.combine(current_start_date, time.min), tz)
            current_end_dt = current_start_dt + timedelta(days=1)
        else:
            lookback_days = 6 if mode == "week" else 29
            candidate_start = anchor_end_date - timedelta(days=lookback_days)
            if requested_start and candidate_start < requested_start:
                candidate_start = requested_start

            current_start_date = candidate_start
            current_end_date = anchor_end_date
            step = timedelta(days=1)
            current_start_dt = timezone.make_aware(datetime.combine(current_start_date, time.min), tz)
            current_end_dt = timezone.make_aware(datetime.combine(current_end_date + timedelta(days=1), time.min), tz)

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

        filtered_agent_ids = _filter_agent_ids(agent_filters_raw, accessible_agent_ids)
        if filtered_agent_ids:
            base_filters["agent_id__in"] = filtered_agent_ids

        if filtered_agent_ids:
            active_agents = [agent for agent in accessible_agents if agent.id in filtered_agent_ids]
        else:
            active_agents = accessible_agents

        trunc_function = TruncHour if step == timedelta(hours=1) else TruncDay

        def _build_counts(start_dt: datetime, end_dt: datetime) -> dict[str, int]:
            filters = base_filters | {
                "created_at__gte": start_dt,
                "created_at__lt": end_dt,
            }
            rows = (
                BrowserUseAgentTask.objects.filter(**filters)
                .annotate(bucket=trunc_function("created_at", tzinfo=tz))
                .values("bucket")
                .order_by("bucket")
                .annotate(count=Count("id"))
            )
            return {row["bucket"].isoformat(): row["count"] for row in rows}

        def _build_agent_counts(start_dt: datetime, end_dt: datetime) -> dict[str, dict[str, int]]:
            filters = base_filters | {
                "created_at__gte": start_dt,
                "created_at__lt": end_dt,
            }
            rows = (
                BrowserUseAgentTask.objects.filter(**filters)
                .annotate(bucket=trunc_function("created_at", tzinfo=tz))
                .values("bucket", "agent_id")
                .order_by("bucket", "agent_id")
                .annotate(count=Count("id"))
            )
            bucket_map: dict[str, dict[str, int]] = {}
            for row in rows:
                bucket = row.get("bucket")
                agent_id = row.get("agent_id")
                if bucket is None or agent_id is None:
                    continue
                bucket_key = bucket.isoformat()
                agent_counts = bucket_map.setdefault(bucket_key, {})
                agent_counts[str(agent_id)] = row["count"]
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

        accessible_agent_ids = {agent.id for agent in _get_accessible_agents(request, organization)}
        filtered_agent_ids = _filter_agent_ids(agent_filters_raw, accessible_agent_ids)

        filters = {
            "step__created_at__gte": start_dt,
            "step__created_at__lte": end_dt,
        }

        if organization is not None:
            filters["step__agent__organization"] = organization
        else:
            filters["step__agent__user"] = request.user
            filters["step__agent__organization__isnull"] = True

        if filtered_agent_ids:
            filters["step__agent__browser_use_agent_id__in"] = filtered_agent_ids

        zero_decimal = Value(Decimal("0"), output_field=DecimalField(max_digits=20, decimal_places=6))

        tool_rows = list(
            PersistentAgentToolCall.objects.filter(**filters)
            .values("tool_name")
            .annotate(
                count=Count("tool_name"),
                credits=Coalesce(Sum("step__credits_cost"), zero_decimal),
            )
            .order_by("-credits", "-count")
        )

        total_count = sum(row["count"] for row in tool_rows)
        total_credits = sum((row["credits"] or Decimal("0")) for row in tool_rows)

        payload = {
            "range": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            },
            "timezone": tz_name,
            "total_count": total_count,
            "total_credits": float(total_credits),
            "tools": [
                {
                    "name": (row["tool_name"] or ""),
                    "count": row["count"],
                    "credits": float(row["credits"] or Decimal("0")),
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
        accessible_agent_ids = {agent.id for agent in accessible_agents}

        filtered_agent_ids = _filter_agent_ids(agent_filters_raw, accessible_agent_ids)
        if filtered_agent_ids:
            active_agent_ids = set(filtered_agent_ids)
            active_agents = [agent for agent in accessible_agents if agent.id in active_agent_ids]
        else:
            active_agent_ids = accessible_agent_ids
            active_agents = accessible_agents

        task_filters = {
            "is_deleted": False,
            "created_at__gte": period_start_dt,
            "created_at__lte": period_end_dt,
            "agent_id__isnull": False,
        }

        if organization is not None:
            task_filters["organization"] = organization
        else:
            task_filters["user"] = request.user
            task_filters["organization__isnull"] = True

        if active_agent_ids:
            task_filters["agent_id__in"] = list(active_agent_ids)

        aggregates = (
            BrowserUseAgentTask.objects.filter(**task_filters)
            .values("agent_id")
            .order_by()
            .annotate(
                total=Count("id"),
                success=Count("id", filter=Q(status=BrowserUseAgentTask.StatusChoices.COMPLETED)),
                error=Count("id", filter=Q(status=BrowserUseAgentTask.StatusChoices.FAILED)),
            )
        )

        aggregate_map: dict[uuid.UUID, dict[str, int]] = {}
        for row in aggregates:
            agent_id = row.get("agent_id")
            if agent_id is None:
                continue
            aggregate_map[agent_id] = {
                "total": row.get("total", 0),
                "success": row.get("success", 0),
                "error": row.get("error", 0),
            }

        period_length_days = max((period_end - period_start).days + 1, 1)

        leaderboard: list[dict[str, object]] = []
        for agent in active_agents:
            stats = aggregate_map.get(agent.id, {"total": 0, "success": 0, "error": 0})
            total = stats["total"]
            success = stats["success"]
            error = stats["error"]
            avg_per_day = float(total) / period_length_days if total > 0 else 0.0
            if total <= 0:
                continue
            leaderboard.append(
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "tasks_total": total,
                    "tasks_per_day": avg_per_day,
                    "success_count": success,
                    "error_count": error,
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

        if organization is not None:
            agents_qs = BrowserUseAgent.objects.filter(persistent_agent__organization=organization)
        else:
            agents_qs = BrowserUseAgent.objects.filter(user=request.user)

        agents = [
            {
                "id": str(agent.id),
                "name": agent.name,
            }
            for agent in agents_qs.order_by("name")
        ]

        return JsonResponse({"agents": agents})
