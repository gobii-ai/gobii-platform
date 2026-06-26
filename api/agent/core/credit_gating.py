import logging
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from django.db import transaction

from api.evals.credit_policy import is_eval_credit_exempt_context
from api.models import PersistentAgent, PersistentAgentStep, PersistentAgentSystemStep
from api.services.agent_error_logging import log_credit_failure
from api.services.signup_preview import can_bypass_task_credit_for_signup_preview
from config import settings
from tasks.services import TaskCreditService
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.constants.task_constants import TASKS_UNLIMITED
from util.tool_costs import get_default_task_credit_cost, get_tool_credit_cost

from .daily_limit_mode import (
    DAILY_LIMIT_MESSAGE_TOOL_NAMES,
    is_daily_hard_limit_message_only_mode,
    is_daily_limit_allowed_tool,
)
from .llm_config import apply_tier_credit_multiplier
from .period_events import (
    DAILY_HARD_LIMIT_BLOCKED_EVENT,
    DAILY_HARD_LIMIT_EXCEEDED_EVENT,
    DAILY_SOFT_LIMIT_EXCEEDED_EVENT,
    should_emit_daily_agent_event,
)
from .prompt_context import get_agent_daily_credit_state

logger = logging.getLogger(__name__)


class CreditBlockReason(str, Enum):
    ACCOUNT_INSUFFICIENT = "account_insufficient"
    DAILY_HARD_LIMIT = "daily_hard_limit"
    CONSUMPTION_FAILED = "consumption_failed"


@dataclass
class CreditSnapshot:
    available: Any = None
    daily_state: dict[str, Any] | None = None
    _legacy: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_value(cls, value: Any) -> "CreditSnapshot | None":
        if value is None:
            return None
        if isinstance(value, CreditSnapshot):
            return value
        if isinstance(value, dict):
            return cls(
                available=value.get("available"),
                daily_state=value.get("daily_state"),
                _legacy=value,
            )
        raise TypeError(f"Unsupported credit snapshot type: {type(value).__name__}")

    def get(self, key: str, default: Any = None) -> Any:
        if key == "available":
            return self.available
        if key == "daily_state":
            return self.daily_state
        return default

    def __getitem__(self, key: str) -> Any:
        if key == "available":
            return self.available
        if key == "daily_state":
            return self.daily_state
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key == "available":
            self.available = value
        elif key == "daily_state":
            self.daily_state = value
        else:
            raise KeyError(key)
        if self._legacy is not None:
            self._legacy[key] = value

    def __contains__(self, key: str) -> bool:
        return key in {"available", "daily_state"} and self.get(key) is not None

    def pop(self, key: str, default: Any = None) -> Any:
        if key == "available":
            old = self.available
            self.available = None
        elif key == "daily_state":
            old = self.daily_state
            self.daily_state = None
        else:
            return default
        if self._legacy is not None:
            self._legacy.pop(key, None)
        return old if old is not None else default

    def as_legacy_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "daily_state": self.daily_state,
        }


@dataclass(frozen=True)
class CreditGrant:
    cost: Decimal | None
    credit: Any


@dataclass(frozen=True)
class CreditBlock:
    reason: CreditBlockReason
    notes: str
    description: str


@dataclass(frozen=True)
class CreditDecision:
    grant: CreditGrant | None = None
    block: CreditBlock | None = None

    @classmethod
    def allow(cls, *, cost: Decimal | None = None, credit: Any = None) -> "CreditDecision":
        return cls(grant=CreditGrant(cost=cost, credit=credit))

    @classmethod
    def deny(
        cls,
        *,
        reason: CreditBlockReason,
        notes: str,
        description: str,
    ) -> "CreditDecision":
        return cls(block=CreditBlock(reason=reason, notes=notes, description=description))

    @property
    def allowed(self) -> bool:
        return self.grant is not None

    @property
    def blocked(self) -> bool:
        return self.block is not None

    @property
    def cost(self) -> Decimal | None:
        return self.grant.cost if self.grant is not None else None

    @property
    def credit(self) -> Any:
        return self.grant.credit if self.grant is not None else None

    def to_legacy(self) -> dict[str, Any] | bool:
        if self.blocked:
            return False
        return {"cost": self.cost, "credit": self.credit}


@dataclass(frozen=True)
class ProcessingCreditState:
    continue_processing: bool
    credit_snapshot: CreditSnapshot | None = None
    skip_reason: CreditBlockReason | None = None


@dataclass(frozen=True)
class _CreditOwnerContext:
    owner: Any
    owner_user: Any
    owner_is_org: bool
    owner_label: str

    @property
    def owner_type(self) -> str:
        return "organization" if self.owner_is_org else "user"


def _get_credit_owner_context(agent: PersistentAgent) -> _CreditOwnerContext:
    owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
    owner_user = getattr(agent, "user", None)
    owner_is_org = TaskCreditService._is_organization_owner(owner) if owner is not None else False
    owner_label = (
        f"organization {getattr(owner, 'id', 'unknown')}"
        if owner_is_org
        else f"user {getattr(owner_user, 'id', 'unknown')}"
    )
    return _CreditOwnerContext(
        owner=owner,
        owner_user=owner_user,
        owner_is_org=owner_is_org,
        owner_label=owner_label,
    )


def _credit_failure_context(
    owner_ctx: _CreditOwnerContext,
    *,
    operation: str,
    tool_name: str,
    cost: Decimal | None = None,
    available: Any = None,
    fallback: str | None = None,
    daily_state: dict | None = None,
) -> dict[str, Any]:
    context = {
        "operation": operation,
        "tool_name": tool_name,
        "owner_label": owner_ctx.owner_label,
        "owner_type": owner_ctx.owner_type,
        "owner_id": str(getattr(owner_ctx.owner, "id", "")) if owner_ctx.owner is not None else None,
        "user_id": str(getattr(owner_ctx.owner_user, "id", "")) if owner_ctx.owner_user is not None else None,
        "cost": str(cost) if cost is not None else None,
        "available": str(available) if available is not None else None,
    }
    if fallback is not None:
        context["fallback"] = fallback
    if daily_state:
        context["daily_hard_limit"] = str(daily_state.get("hard_limit")) if daily_state.get("hard_limit") is not None else None
        context["daily_hard_remaining"] = str(daily_state.get("hard_limit_remaining")) if daily_state.get("hard_limit_remaining") is not None else None
    return context


def _create_process_system_step(
    *,
    agent: PersistentAgent,
    description: str,
    notes: str,
    code: str = PersistentAgentSystemStep.Code.PROCESS_EVENTS,
) -> PersistentAgentStep:
    step = PersistentAgentStep.objects.create(
        agent=agent,
        description=description,
    )
    PersistentAgentSystemStep.objects.create(
        step=step,
        code=code,
        notes=notes,
    )
    return step


def get_credit_snapshot_daily_state(
    agent: PersistentAgent,
    credit_snapshot: Any,
) -> dict:
    snapshot = CreditSnapshot.from_value(credit_snapshot)
    daily_state = snapshot.daily_state if snapshot is not None else None
    if daily_state is None:
        daily_state = get_agent_daily_credit_state(agent)
        if snapshot is not None:
            snapshot["daily_state"] = daily_state
    return daily_state


def _update_cached_daily_state_after_credit(daily_state: dict, cost: Decimal | None) -> None:
    if cost is None or not isinstance(daily_state, dict):
        return
    used_value = daily_state.get("used", Decimal("0"))
    if not isinstance(used_value, Decimal):
        used_value = Decimal(str(used_value))
    new_used = used_value + cost
    daily_state["used"] = new_used

    hard_limit_value = daily_state.get("hard_limit")
    if hard_limit_value is not None:
        hard_remaining_after = hard_limit_value - new_used
        daily_state["hard_limit_remaining"] = (
            hard_remaining_after if hard_remaining_after > Decimal("0") else Decimal("0")
        )
    soft_target_value = daily_state.get("soft_target")
    if soft_target_value is not None:
        soft_remaining_after = soft_target_value - new_used
        soft_remaining_after = (
            soft_remaining_after if soft_remaining_after > Decimal("0") else Decimal("0")
        )
        daily_state["soft_target_remaining"] = soft_remaining_after
        daily_state["soft_target_exceeded"] = soft_remaining_after <= Decimal("0")


def _has_sufficient_daily_credit(state: dict, cost: Decimal | None) -> bool:
    if cost is None:
        return True

    hard_limit = state.get("hard_limit")
    if hard_limit is None:
        return True

    remaining = state.get("hard_limit_remaining")
    if remaining is None:
        try:
            used = state.get("used", Decimal("0"))
            if not isinstance(used, Decimal):
                used = Decimal(str(used))
            remaining = hard_limit - used
        except Exception as exc:
            logger.warning("Failed to derive hard limit remaining: %s", exc)
            remaining = Decimal("0")

    try:
        return remaining >= cost
    except TypeError as exc:
        logger.warning("Type error during daily credit check: %s", exc)
        return False


def _emit_soft_limit_exceeded_once(
    *,
    agent: PersistentAgent,
    owner_user,
    tool_name: str,
    daily_state: dict,
    soft_target,
    soft_target_remaining,
    span=None,
) -> None:
    if not should_emit_daily_agent_event(agent.id, DAILY_SOFT_LIMIT_EXCEEDED_EVENT):
        return

    logger.info(
        "Agent %s exceeded daily soft target (used=%s target=%s)",
        agent.id,
        daily_state.get("used"),
        soft_target,
    )
    try:
        analytics_props: dict[str, Any] = {
            "agent_id": str(agent.id),
            "agent_name": agent.name,
            "tool_name": tool_name,
            "message_type": "task_credits_low",
            "medium": "backend",
        }
        if soft_target is not None:
            analytics_props["soft_target"] = str(soft_target)
        used_value = daily_state.get("used")
        if used_value is not None:
            analytics_props["credits_used_today"] = str(used_value)
        if soft_target_remaining is not None:
            analytics_props["soft_target_remaining"] = str(soft_target_remaining)
        props_with_org = Analytics.with_org_properties(
            analytics_props,
            organization=getattr(agent, "organization", None),
        )
        Analytics.track_event(
            user_id=owner_user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_SOFT_LIMIT_EXCEEDED,
            source=AnalyticsSource.AGENT,
            properties=props_with_org,
        )
    except Exception:
        logger.exception(
            "Failed to emit analytics for agent %s soft target exceedance",
            agent.id,
        )
    if span is not None:
        try:
            span.add_event("Soft target exceeded")
        except Exception:
            pass


def _emit_hard_limit_exceeded_once(
    *,
    agent: PersistentAgent,
    owner_user,
    tool_name: str,
    daily_state: dict,
    hard_limit,
    hard_remaining,
) -> None:
    if not should_emit_daily_agent_event(agent.id, DAILY_HARD_LIMIT_EXCEEDED_EVENT):
        return

    try:
        analytics_props: dict[str, Any] = {
            "agent_id": str(agent.id),
            "agent_name": agent.name,
            "tool_name": tool_name,
            "message_type": "daily_hard_limit",
            "medium": "backend",
        }
        if hard_limit is not None:
            analytics_props["hard_limit"] = str(hard_limit)
        used_value = daily_state.get("used")
        if used_value is not None:
            analytics_props["credits_used_today"] = str(used_value)
        if hard_remaining is not None:
            analytics_props["hard_limit_remaining"] = str(hard_remaining)
        props_with_org = Analytics.with_org_properties(
            analytics_props,
            organization=getattr(agent, "organization", None),
        )
        Analytics.track_event(
            user_id=owner_user.id,
            event=AnalyticsEvent.PERSISTENT_AGENT_HARD_LIMIT_EXCEEDED,
            source=AnalyticsSource.AGENT,
            properties=props_with_org,
        )
    except Exception:
        logger.exception(
            "Failed to emit analytics for agent %s hard limit exceedance",
            agent.id,
        )


def create_daily_hard_limit_system_step_once(
    *,
    agent: PersistentAgent,
    description: str,
    notes: str,
) -> None:
    if not should_emit_daily_agent_event(agent.id, DAILY_HARD_LIMIT_BLOCKED_EVENT):
        return

    _create_process_system_step(
        agent=agent,
        description=description,
        notes=notes,
    )


def prepare_processing_credit_state(
    agent: PersistentAgent,
    *,
    budget_ctx: Any = None,
    span=None,
) -> ProcessingCreditState:
    if is_eval_credit_exempt_context(agent=agent, eval_run_id=getattr(budget_ctx, "eval_run_id", None)):
        if span is not None:
            span.add_event("Eval credit gate bypassed")
            span.set_attribute("credit_check.eval_bypass", True)
        return ProcessingCreditState(
            continue_processing=True,
            credit_snapshot=CreditSnapshot(available=None, daily_state={}),
        )

    if not settings.GOBII_PROPRIETARY_MODE:
        if span is not None:
            span.add_event("Proprietary mode disabled; skipping credit gate")
        return ProcessingCreditState(continue_processing=True)

    owner_ctx = _get_credit_owner_context(agent)
    owner = owner_ctx.owner
    owner_user = owner_ctx.owner_user
    if owner is None:
        if span is not None:
            span.add_event("Agent has no owner; skipping credit gate")
        return ProcessingCreditState(continue_processing=True)

    if can_bypass_task_credit_for_signup_preview(agent):
        if span is not None:
            span.add_event("Signup preview credit gate bypassed")
            span.set_attribute("credit_check.signup_preview_bypass", True)
        return ProcessingCreditState(
            continue_processing=True,
            credit_snapshot=CreditSnapshot(available=None, daily_state={}),
        )

    try:
        available = TaskCreditService.calculate_available_tasks_for_owner(owner)
    except Exception as exc:
        logger.error(
            "Credit availability check failed for agent %s (%s): %s",
            agent.id,
            owner_ctx.owner_label,
            str(exc),
        )
        available = None

    if span is not None:
        span.set_attribute("credit_check.available", int(available) if available is not None else 0)
        span.set_attribute("credit_check.proprietary_mode", True)
        span.set_attribute("credit_check.owner_type", owner_ctx.owner_type)
        if owner_ctx.owner_is_org:
            span.set_attribute("credit_check.organization_id", str(getattr(owner, "id", None)))
        if owner_user is not None:
            span.set_attribute("credit_check.user_id", owner_user.id)

    daily_state = get_agent_daily_credit_state(agent)
    daily_limit = daily_state.get("hard_limit")
    daily_remaining = daily_state.get("hard_limit_remaining")
    snapshot = CreditSnapshot(available=available, daily_state=daily_state)

    if span is not None:
        try:
            span.set_attribute(
                "credit_check.daily_limit",
                float(daily_limit) if daily_limit is not None else -1.0,
            )
            span.set_attribute(
                "credit_check.daily_remaining_before_loop",
                float(daily_remaining) if daily_remaining is not None else -1.0,
            )
        except Exception:
            pass

    daily_limit_exhausted = daily_limit is not None and (
        daily_remaining is None or daily_remaining <= Decimal("0")
    )
    if daily_limit_exhausted:
        msg = "Agent reached its enforced daily task credit limit and is entering message-only mode."
        logger.warning(
            "Persistent agent %s reached hard daily limit before loop; continuing in message-only mode (used=%s limit=%s).",
            agent.id,
            daily_state.get("used"),
            daily_limit,
        )

        create_daily_hard_limit_system_step_once(
            agent=agent,
            description=msg,
            notes="daily_credit_limit_exhausted",
        )

        if span is not None:
            span.add_event("Agent processing entering daily-limit message-only mode")
            span.set_attribute("credit_check.daily_limit_block", True)

    if (
        not daily_limit_exhausted
        and available is not None
        and available != TASKS_UNLIMITED
        and Decimal(available) <= Decimal("0")
    ):
        msg = "Skipped processing due to insufficient credits (proprietary mode)."
        logger.warning(
            "Persistent agent %s not processed - %s has no remaining task credits.",
            agent.id,
            owner_ctx.owner_label,
        )

        _create_process_system_step(
            agent=agent,
            description=msg,
            notes="credit_insufficient",
        )

        if span is not None:
            span.add_event("Agent processing skipped - insufficient credits")
            span.set_attribute("credit_check.sufficient", False)
        return ProcessingCreditState(
            continue_processing=False,
            credit_snapshot=snapshot,
            skip_reason=CreditBlockReason.ACCOUNT_INSUFFICIENT,
        )

    return ProcessingCreditState(continue_processing=True, credit_snapshot=snapshot)


def reserve_tool_credit(
    agent: PersistentAgent,
    tool_name: str,
    *,
    span=None,
    credit_snapshot: Any = None,
    eval_run_id: str | None = None,
) -> CreditDecision:
    if tool_name == "send_chat_message":
        return CreditDecision.allow()

    if is_eval_credit_exempt_context(agent=agent, eval_run_id=eval_run_id):
        if span is not None:
            try:
                span.add_event("Eval credit bypass active")
                span.set_attribute("credit_check.eval_bypass", True)
            except Exception:
                pass
        return CreditDecision.allow()

    owner_ctx = _get_credit_owner_context(agent)
    owner = owner_ctx.owner
    owner_user = owner_ctx.owner_user

    if not settings.GOBII_PROPRIETARY_MODE or owner is None:
        return CreditDecision.allow()

    if can_bypass_task_credit_for_signup_preview(agent):
        if span is not None:
            try:
                span.add_event("Signup preview credit bypass active")
            except Exception:
                pass
        return CreditDecision.allow()

    snapshot = CreditSnapshot.from_value(credit_snapshot)
    consumed: dict | None = None
    consumed_credit = None

    try:
        cost = get_tool_credit_cost(tool_name)
    except Exception as exc:
        log_credit_failure(
            agent,
            exc,
            source="api.agent.core.credit_gating.reserve_tool_credit.cost",
            logger=logger,
            context=_credit_failure_context(
                owner_ctx,
                operation="get_tool_credit_cost",
                tool_name=tool_name,
                fallback="default_task_credit_cost",
            ),
        )
        cost = get_default_task_credit_cost()

    if cost is not None:
        cost = apply_tier_credit_multiplier(agent, cost)

    if snapshot is not None and "available" in snapshot:
        available = snapshot.get("available")
    else:
        try:
            available = TaskCreditService.calculate_available_tasks_for_owner(owner)
        except Exception as exc:
            log_credit_failure(
                agent,
                exc,
                source="api.agent.core.credit_gating.reserve_tool_credit.availability",
                logger=logger,
                context=_credit_failure_context(
                    owner_ctx,
                    operation="calculate_available_tasks",
                    tool_name=tool_name,
                    cost=cost,
                ),
            )
            available = None
        if snapshot is not None:
            snapshot["available"] = available

    daily_state = get_credit_snapshot_daily_state(agent, snapshot)

    if is_daily_hard_limit_message_only_mode(daily_state) and is_daily_limit_allowed_tool(tool_name):
        if (
            tool_name in DAILY_LIMIT_MESSAGE_TOOL_NAMES
            and available is not None
            and available != TASKS_UNLIMITED
            and Decimal(available) <= Decimal("0")
        ):
            msg_desc = f"Skipped tool '{tool_name}' due to insufficient credits mid-loop."
            _create_process_system_step(
                agent=agent,
                description=msg_desc,
                notes="credit_insufficient_mid_loop",
            )
            if span is not None:
                try:
                    span.add_event("Tool skipped - insufficient credits mid-loop")
                except Exception:
                    pass
            logger.warning(
                "Agent %s insufficient credits mid-loop while in daily-limit message-only mode.",
                agent.id,
            )
            return CreditDecision.deny(
                reason=CreditBlockReason.ACCOUNT_INSUFFICIENT,
                notes="credit_insufficient_mid_loop",
                description=msg_desc,
            )
        if span is not None:
            try:
                span.add_event("Tool allowed in daily-limit message-only mode")
                span.set_attribute("credit_check.daily_limit_message_only_mode", True)
            except Exception:
                pass
        return CreditDecision.allow()

    hard_limit = daily_state.get("hard_limit")
    hard_remaining = daily_state.get("hard_limit_remaining")
    soft_target = daily_state.get("soft_target")
    soft_target_remaining = daily_state.get("soft_target_remaining")
    soft_exceeded = daily_state.get("soft_target_exceeded")

    if soft_exceeded and not daily_state.get("soft_target_warning_logged"):
        daily_state["soft_target_warning_logged"] = True
        _emit_soft_limit_exceeded_once(
            agent=agent,
            owner_user=owner_user,
            tool_name=tool_name,
            daily_state=daily_state,
            soft_target=soft_target,
            soft_target_remaining=soft_target_remaining,
            span=span,
        )

    if span is not None:
        try:
            span.set_attribute(
                "credit_check.available_in_loop",
                int(available) if available is not None else -2,
            )
        except Exception as exc:
            logger.debug("Failed to set soft target span attributes: %s", exc)
        try:
            span.set_attribute("credit_check.owner_type", owner_ctx.owner_type)
            if owner_ctx.owner_is_org:
                span.set_attribute("credit_check.organization_id", str(getattr(owner, "id", None)))
            if owner_user is not None:
                span.set_attribute("credit_check.user_id", str(owner_user.id))
        except Exception as exc:
            logger.debug("Failed to set owner span attributes: %s", exc)
        try:
            span.set_attribute(
                "credit_check.tool_cost",
                float(cost) if cost is not None else float(get_default_task_credit_cost()),
            )
        except Exception as exc:
            logger.debug("Failed to set span attribute 'credit_check.tool_cost': %s", exc)
        try:
            span.set_attribute(
                "credit_check.daily_limit",
                float(hard_limit) if hard_limit is not None else -1.0,
            )
        except Exception as exc:
            logger.debug("Failed to set span attribute 'credit_check.daily_limit': %s", exc)
        try:
            span.set_attribute(
                "credit_check.daily_remaining_before",
                float(hard_remaining) if hard_remaining is not None else -1.0,
            )
        except Exception as exc:
            logger.debug("Failed to set span attribute 'credit_check.daily_remaining_before': %s", exc)
        try:
            span.set_attribute(
                "credit_check.daily_soft_target",
                float(soft_target) if soft_target is not None else -1.0,
            )
            span.set_attribute(
                "credit_check.daily_soft_remaining",
                float(soft_target_remaining) if soft_target_remaining is not None else -1.0,
            )
            span.set_attribute("credit_check.daily_soft_exceeded", bool(soft_exceeded))
        except Exception:
            pass

    if not _has_sufficient_daily_credit(daily_state, cost):
        if not daily_state.get("hard_limit_warning_logged"):
            daily_state["hard_limit_warning_logged"] = True
            _emit_hard_limit_exceeded_once(
                agent=agent,
                owner_user=owner_user,
                tool_name=tool_name,
                daily_state=daily_state,
                hard_limit=hard_limit,
                hard_remaining=hard_remaining,
            )
        limit_display = hard_limit
        used_display = daily_state.get("used")
        msg_desc = f"Skipped tool '{tool_name}' because this agent reached its enforced daily credit limit for today."
        if limit_display is not None:
            msg_desc += f" {used_display} of {limit_display} credits already used."

        create_daily_hard_limit_system_step_once(
            agent=agent,
            description=msg_desc,
            notes="daily_credit_limit_mid_loop",
        )
        if span is not None:
            try:
                span.add_event("Tool skipped - daily credit limit reached")
                span.set_attribute("credit_check.daily_limit_block", True)
            except Exception:
                pass
        logger.warning(
            "Agent %s skipped tool %s due to daily credit limit (used=%s limit=%s)",
            agent.id,
            tool_name,
            used_display,
            limit_display,
        )
        return CreditDecision.deny(
            reason=CreditBlockReason.DAILY_HARD_LIMIT,
            notes="daily_credit_limit_mid_loop",
            description=msg_desc,
        )

    if (
        available is not None
        and available != TASKS_UNLIMITED
        and cost is not None
        and Decimal(available) < cost
    ):
        msg_desc = f"Skipped tool '{tool_name}' due to insufficient credits mid-loop."
        _create_process_system_step(
            agent=agent,
            description=msg_desc,
            notes="credit_insufficient_mid_loop",
        )
        if span is not None:
            try:
                span.add_event("Tool skipped - insufficient credits mid-loop")
            except Exception:
                pass
        logger.warning(
            "Agent %s insufficient credits mid-loop; halting further processing.",
            agent.id,
        )
        return CreditDecision.deny(
            reason=CreditBlockReason.ACCOUNT_INSUFFICIENT,
            notes="credit_insufficient_mid_loop",
            description=msg_desc,
        )

    try:
        with transaction.atomic():
            consumed = TaskCreditService.check_and_consume_credit_for_owner(owner, amount=cost)
            consumed_credit = consumed.get("credit") if consumed else None
    except Exception as exc:
        log_credit_failure(
            agent,
            exc,
            source="api.agent.core.credit_gating.reserve_tool_credit",
            logger=logger,
            context=_credit_failure_context(
                owner_ctx,
                operation="consume_credit",
                tool_name=tool_name,
                cost=cost,
                available=available,
                daily_state=daily_state,
            ),
        )
        if span is not None:
            try:
                span.add_event("Credit consumption raised exception", {"error": str(exc)})
                span.set_attribute("credit_check.error", str(exc))
            except Exception:
                pass

    if span is not None:
        try:
            span.set_attribute("credit_check.consumed_in_loop", bool(consumed and consumed.get("success")))
        except Exception:
            pass
    if not consumed or not consumed.get("success"):
        msg_desc = f"Skipped tool '{tool_name}' due to insufficient credits during processing."
        _create_process_system_step(
            agent=agent,
            description=msg_desc,
            notes="credit_consumption_failure_mid_loop",
        )
        if span is not None:
            try:
                span.add_event("Tool skipped - insufficient credits during processing")
            except Exception:
                pass
        logger.warning(
            "Agent %s encountered insufficient credits during processing; halting further processing.",
            agent.id,
        )
        return CreditDecision.deny(
            reason=CreditBlockReason.CONSUMPTION_FAILED,
            notes="credit_consumption_failure_mid_loop",
            description=msg_desc,
        )

    if cost is not None and isinstance(daily_state, dict):
        try:
            _update_cached_daily_state_after_credit(daily_state, cost)
        except Exception:
            logger.debug(
                "Failed to update cached daily_state after consuming credit for agent %s",
                agent.id,
                exc_info=True,
            )

    if snapshot is not None:
        snapshot["daily_state"] = daily_state
        snapshot.pop("available", None)

    if span is not None:
        try:
            remaining_after = (
                daily_state.get("hard_limit_remaining") if isinstance(daily_state, dict) else None
            )
            span.set_attribute(
                "credit_check.daily_remaining_after",
                float(remaining_after) if remaining_after is not None else -1.0,
            )
        except Exception:
            pass

    return CreditDecision.allow(cost=cost, credit=consumed_credit)
