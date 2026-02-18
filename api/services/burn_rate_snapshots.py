from datetime import timedelta
from decimal import Decimal
from typing import Iterable

from django.conf import settings
from django.db.models import Case, DecimalField, F, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from api.models import BrowserUseAgentTask, BurnRateSnapshot, PersistentAgent, PersistentAgentStep

DECIMAL_ZERO = Decimal("0")
API_CREDIT_DECIMAL = Decimal("1")
EVAL_ENVIRONMENT = "eval"


def _per_task_credit_expression() -> Case:
    zero_decimal = Value(DECIMAL_ZERO, output_field=DecimalField(max_digits=20, decimal_places=6))
    return Case(
        When(
            agent_id__isnull=True,
            then=Coalesce(
                F("credits_cost"),
                Value(API_CREDIT_DECIMAL, output_field=DecimalField(max_digits=20, decimal_places=6)),
            ),
        ),
        default=Coalesce(F("credits_cost"), zero_decimal),
        output_field=DecimalField(max_digits=20, decimal_places=6),
    )


def _exclude_eval_browser_tasks(qs):
    return qs.exclude(agent__persistent_agent__execution_environment=EVAL_ENVIRONMENT)


def _exclude_eval_persistent_steps(qs):
    return qs.exclude(agent__execution_environment=EVAL_ENVIRONMENT)


def _normalize_windows(windows: Iterable[int] | None) -> list[int]:
    if windows is None:
        windows = settings.BURN_RATE_SNAPSHOT_WINDOWS_MINUTES

    normalized: list[int] = []
    for raw in windows:
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            continue
        if minutes > 0:
            normalized.append(minutes)
    return sorted(set(normalized))


def _add_total(target: dict, key: tuple, amount: Decimal) -> None:
    if amount is None:
        return
    target[key] = target.get(key, DECIMAL_ZERO) + amount


def _compute_rates(total: Decimal, window_minutes: int) -> tuple[Decimal, Decimal]:
    if window_minutes <= 0:
        return DECIMAL_ZERO, DECIMAL_ZERO
    hours = Decimal(str(window_minutes)) / Decimal("60")
    if hours <= DECIMAL_ZERO:
        return DECIMAL_ZERO, DECIMAL_ZERO
    per_hour = total / hours if total is not None else DECIMAL_ZERO
    per_day = per_hour * Decimal("24")
    return per_hour, per_day


def _collect_task_totals(window_start, window_end) -> tuple[dict, dict]:
    zero_value = Value(DECIMAL_ZERO, output_field=DecimalField(max_digits=20, decimal_places=6))
    credit_expr = _per_task_credit_expression()
    tasks_qs = BrowserUseAgentTask.objects.alive().filter(
        created_at__gte=window_start,
        created_at__lt=window_end,
    )
    tasks_qs = _exclude_eval_browser_tasks(tasks_qs)

    owner_totals: dict[tuple[str, object], Decimal] = {}
    owner_rows = tasks_qs.values("organization_id", "user_id").annotate(
        total=Coalesce(Sum(credit_expr), zero_value),
    )
    for row in owner_rows:
        total = row.get("total") or DECIMAL_ZERO
        org_id = row.get("organization_id")
        user_id = row.get("user_id")
        if org_id:
            _add_total(owner_totals, (BurnRateSnapshot.ScopeType.ORGANIZATION, org_id), total)
        elif user_id:
            _add_total(owner_totals, (BurnRateSnapshot.ScopeType.USER, user_id), total)

    agent_totals: dict[tuple[object], Decimal] = {}
    agent_rows = (
        tasks_qs.filter(agent__persistent_agent__isnull=False)
        .values("agent__persistent_agent__id")
        .annotate(total=Coalesce(Sum(credit_expr), zero_value))
    )
    for row in agent_rows:
        agent_id = row.get("agent__persistent_agent__id")
        if agent_id:
            _add_total(agent_totals, (agent_id,), row.get("total") or DECIMAL_ZERO)

    return owner_totals, agent_totals


def _collect_step_totals(window_start, window_end) -> tuple[dict, dict]:
    zero_value = Value(DECIMAL_ZERO, output_field=DecimalField(max_digits=20, decimal_places=6))
    steps_qs = PersistentAgentStep.objects.filter(
        created_at__gte=window_start,
        created_at__lt=window_end,
    )
    steps_qs = _exclude_eval_persistent_steps(steps_qs)

    owner_totals: dict[tuple[str, object], Decimal] = {}
    owner_rows = steps_qs.values("agent__organization_id", "agent__user_id").annotate(
        total=Coalesce(Sum("credits_cost"), zero_value),
    )
    for row in owner_rows:
        total = row.get("total") or DECIMAL_ZERO
        org_id = row.get("agent__organization_id")
        user_id = row.get("agent__user_id")
        if org_id:
            _add_total(owner_totals, (BurnRateSnapshot.ScopeType.ORGANIZATION, org_id), total)
        elif user_id:
            _add_total(owner_totals, (BurnRateSnapshot.ScopeType.USER, user_id), total)

    agent_totals: dict[tuple[object], Decimal] = {}
    agent_rows = steps_qs.values("agent_id").annotate(
        total=Coalesce(Sum("credits_cost"), zero_value),
    )
    for row in agent_rows:
        agent_id = row.get("agent_id")
        if agent_id:
            _add_total(agent_totals, (agent_id,), row.get("total") or DECIMAL_ZERO)

    return owner_totals, agent_totals


def _merge_totals(base: dict, additional: dict) -> None:
    for key, value in additional.items():
        base[key] = base.get(key, DECIMAL_ZERO) + value


def _build_snapshots(
    *,
    owner_totals: dict,
    agent_totals: dict,
    window_minutes: int,
    window_start,
    window_end,
    computed_at,
) -> list[BurnRateSnapshot]:
    snapshots: list[BurnRateSnapshot] = []

    for (scope_type, owner_id), total in owner_totals.items():
        per_hour, per_day = _compute_rates(total, window_minutes)
        snapshot = BurnRateSnapshot(
            scope_type=scope_type,
            scope_id=str(owner_id),
            user_id=owner_id if scope_type == BurnRateSnapshot.ScopeType.USER else None,
            organization_id=owner_id if scope_type == BurnRateSnapshot.ScopeType.ORGANIZATION else None,
            window_minutes=window_minutes,
            window_start=window_start,
            window_end=window_end,
            window_total=total,
            burn_rate_per_hour=per_hour,
            burn_rate_per_day=per_day,
            computed_at=computed_at,
        )
        snapshots.append(snapshot)

    agent_ids = [key[0] for key in agent_totals.keys()]
    agent_owner_map: dict[object, tuple[object | None, object | None]] = {}
    if agent_ids:
        for row in PersistentAgent.objects.filter(id__in=agent_ids).values("id", "user_id", "organization_id"):
            agent_owner_map[row["id"]] = (row.get("user_id"), row.get("organization_id"))

    for (agent_id,), total in agent_totals.items():
        owner_user_id, owner_org_id = agent_owner_map.get(agent_id, (None, None))
        per_hour, per_day = _compute_rates(total, window_minutes)
        snapshot = BurnRateSnapshot(
            scope_type=BurnRateSnapshot.ScopeType.AGENT,
            scope_id=str(agent_id),
            agent_id=agent_id,
            user_id=owner_user_id,
            organization_id=owner_org_id,
            window_minutes=window_minutes,
            window_start=window_start,
            window_end=window_end,
            window_total=total,
            burn_rate_per_hour=per_hour,
            burn_rate_per_day=per_day,
            computed_at=computed_at,
        )
        snapshots.append(snapshot)

    return snapshots


def refresh_burn_rate_snapshots(*, windows_minutes: Iterable[int] | None = None, now=None) -> int:
    windows = _normalize_windows(windows_minutes)
    if not windows:
        return 0

    window_end = now or timezone.now()
    snapshots: list[BurnRateSnapshot] = []

    for window_minutes in windows:
        window_start = window_end - timedelta(minutes=window_minutes)
        task_owner_totals, task_agent_totals = _collect_task_totals(window_start, window_end)
        step_owner_totals, step_agent_totals = _collect_step_totals(window_start, window_end)

        owner_totals: dict[tuple[str, object], Decimal] = {}
        agent_totals: dict[tuple[object], Decimal] = {}
        _merge_totals(owner_totals, task_owner_totals)
        _merge_totals(owner_totals, step_owner_totals)
        _merge_totals(agent_totals, task_agent_totals)
        _merge_totals(agent_totals, step_agent_totals)

        snapshots.extend(
            _build_snapshots(
                owner_totals=owner_totals,
                agent_totals=agent_totals,
                window_minutes=window_minutes,
                window_start=window_start,
                window_end=window_end,
                computed_at=window_end,
            )
        )

    if not snapshots:
        return 0

    BurnRateSnapshot.objects.bulk_create(
        snapshots,
        update_conflicts=True,
        update_fields=[
            "user",
            "organization",
            "agent",
            "window_start",
            "window_end",
            "window_total",
            "burn_rate_per_hour",
            "burn_rate_per_day",
            "computed_at",
        ],
        unique_fields=["scope_type", "scope_id", "window_minutes"],
    )

    return len(snapshots)


def resolve_owner_scope(owner) -> tuple[str, str]:
    model_name = getattr(getattr(owner, "_meta", None), "model_name", None)
    if model_name == "organization":
        return BurnRateSnapshot.ScopeType.ORGANIZATION, str(owner.id)
    return BurnRateSnapshot.ScopeType.USER, str(owner.id)


def get_burn_rate_snapshot(
    *,
    scope_type: str,
    scope_id: str,
    window_minutes: int,
    max_age_minutes: int | None = None,
) -> BurnRateSnapshot | None:
    if not scope_type or not scope_id:
        return None
    try:
        minutes = int(window_minutes)
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    snapshot = (
        BurnRateSnapshot.objects.filter(
            scope_type=scope_type,
            scope_id=str(scope_id),
            window_minutes=minutes,
        )
        .order_by("-computed_at")
        .first()
    )
    if snapshot is None:
        return None
    if max_age_minutes is not None and max_age_minutes > 0:
        cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
        if snapshot.computed_at < cutoff:
            return None
    return snapshot


def get_burn_rate_snapshot_for_owner(
    owner,
    *,
    window_minutes: int,
    max_age_minutes: int | None = None,
) -> BurnRateSnapshot | None:
    scope_type, scope_id = resolve_owner_scope(owner)
    return get_burn_rate_snapshot(
        scope_type=scope_type,
        scope_id=scope_id,
        window_minutes=window_minutes,
        max_age_minutes=max_age_minutes,
    )


def get_burn_rate_snapshot_for_agent(
    agent: PersistentAgent,
    *,
    window_minutes: int,
    max_age_minutes: int | None = None,
) -> BurnRateSnapshot | None:
    return get_burn_rate_snapshot(
        scope_type=BurnRateSnapshot.ScopeType.AGENT,
        scope_id=str(agent.id),
        window_minutes=window_minutes,
        max_age_minutes=max_age_minutes,
    )


def serialize_burn_rate_snapshot(snapshot: BurnRateSnapshot | None) -> dict | None:
    if snapshot is None:
        return None
    return {
        "scope_type": snapshot.scope_type,
        "scope_id": snapshot.scope_id,
        "window_minutes": snapshot.window_minutes,
        "window_start": snapshot.window_start.isoformat(),
        "window_end": snapshot.window_end.isoformat(),
        "window_total": float(snapshot.window_total),
        "burn_rate_per_hour": float(snapshot.burn_rate_per_hour),
        "burn_rate_per_day": float(snapshot.burn_rate_per_day),
        "computed_at": snapshot.computed_at.isoformat(),
    }
