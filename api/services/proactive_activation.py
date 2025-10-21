import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import List, Sequence

from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone
from waffle import get_waffle_flag_model

from config.redis_client import get_redis_client
from api.models import PersistentAgent, PersistentAgentStep, PersistentAgentSystemStep
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

logger = logging.getLogger(__name__)


@dataclass
class ProactiveTriggerResult:
    agent: PersistentAgent
    step: PersistentAgentStep
    metadata: dict


class ProactiveActivationService:
    """Select and queue proactive activations for eligible agents."""

    DEFAULT_BATCH_SIZE = 10
    SCAN_LIMIT = 50
    ROLLOUT_FLAG_NAME = "proactive_agent_rollout"
    USER_COOLDOWN_FALLBACK_MINUTES = 360
    MIN_TRIGGER_INTERVAL_MINUTES = 7 * 24 * 60  # At most once per week
    MIN_ACTIVITY_COOLDOWN = timedelta(days=3)  # Wait at least three days since last interaction

    @classmethod
    def trigger_agents(cls, *, batch_size: int | None = None) -> List[PersistentAgent]:
        """Select eligible agents, record proactive metadata, and return triggered agents."""
        batch = batch_size or cls.DEFAULT_BATCH_SIZE
        now = timezone.now()
        redis_client = cls._get_redis_client()

        candidates = cls._eligible_agents(now)
        triggered_results: List[ProactiveTriggerResult] = []
        seen_users: set[str] = set()

        for agent in candidates:
            if agent.user_id in seen_users:
                continue
            if not cls._is_rollout_enabled_for_agent(agent):
                logger.debug(
                    "Skipping proactive trigger for agent %s because rollout flag '%s' is inactive",
                    agent.id,
                    cls.ROLLOUT_FLAG_NAME,
                )
                continue
            if not cls._recent_activity_cooldown_satisfied(agent, now):
                continue
            has_credit, credit_remaining = cls._has_required_daily_credit(agent)
            if not has_credit:
                logger.debug("Skipping proactive trigger for agent %s due to insufficient daily credits", agent.id)
                continue
            effective_min_interval = cls._effective_min_interval_minutes(agent)
            if not cls._min_interval_satisfied(agent, now, effective_min_interval):
                continue
            if not cls._acquire_user_gate(redis_client, agent.user_id, effective_min_interval):
                continue

            metadata = cls._build_metadata(now, remaining_credits=credit_remaining)

            try:
                result = cls._record_trigger(agent, now, metadata)
            except Exception:
                logger.exception("Failed to record proactive trigger for agent %s", agent.id)
                cls._release_user_gate(redis_client, agent.user_id)
                continue

            triggered_results.append(result)
            seen_users.add(agent.user_id)

            if len(triggered_results) >= batch:
                break

        return [result.agent for result in triggered_results]

    @classmethod
    def _eligible_agents(cls, now: datetime) -> Sequence[PersistentAgent]:
        """Return a ranked list of potentially eligible agents."""
        day_start = now.astimezone(timezone.get_current_timezone()).replace(hour=0, minute=0, second=0, microsecond=0)

        qs = (
            PersistentAgent.objects.filter(
                proactive_opt_in=True,
                is_active=True,
                life_state=PersistentAgent.LifeState.ACTIVE,
            )
            .select_related("user", "browser_use_agent")
            .annotate(
                proactive_today=Count(
                    "steps__system_step",
                    filter=Q(
                        steps__system_step__code=PersistentAgentSystemStep.Code.PROACTIVE_TRIGGER,
                        steps__created_at__gte=day_start,
                    ),
                    distinct=True,
                )
            )
            .filter(
                Q(proactive_max_daily__lte=0) | Q(proactive_today__lt=F("proactive_max_daily"))
            )
            .order_by(F("proactive_last_trigger_at").asc(nulls_first=True), "last_interaction_at", "created_at")[: cls.SCAN_LIMIT]
        )

        return list(qs)

    @classmethod
    def _effective_min_interval_minutes(cls, agent: PersistentAgent) -> int:
        """Apply global guardrails to per-agent interval settings."""
        minutes = int(agent.proactive_min_interval_minutes or 0)
        return max(minutes, cls.MIN_TRIGGER_INTERVAL_MINUTES)

    @staticmethod
    def _min_interval_satisfied(agent: PersistentAgent, now: datetime, required_minutes: int) -> bool:
        """Check per-agent cooldown window."""
        if required_minutes <= 0:
            return True
        last = agent.proactive_last_trigger_at
        if not last:
            return True
        delta = now - last
        return delta >= timedelta(minutes=required_minutes)

    @classmethod
    def _recent_activity_cooldown_satisfied(cls, agent: PersistentAgent, now: datetime) -> bool:
        """Ensure we allow a quiet period after the last human interaction."""
        anchor = agent.last_interaction_at or agent.created_at
        if not anchor:
            return True
        return now - anchor >= cls.MIN_ACTIVITY_COOLDOWN

    @staticmethod
    def _daily_credit_remaining(agent: PersistentAgent) -> Decimal | None:
        """Return remaining daily task credits for the agent (None means unlimited)."""
        try:
            return agent.get_daily_credit_remaining()
        except Exception:
            logger.exception("Failed to compute daily credit remaining for agent %s", agent.id)
            return Decimal("0")

    @classmethod
    def _has_required_daily_credit(cls, agent: PersistentAgent) -> tuple[bool, Decimal | None]:
        """Check that the agent has at least one remaining task credit."""
        remaining = cls._daily_credit_remaining(agent)
        if remaining is None:
            return True, None
        try:
            return remaining >= Decimal("1"), remaining
        except (TypeError, InvalidOperation):
            logger.warning("Invalid daily credit remaining value for agent %s; skipping proactive trigger.", agent.id)
            return False, Decimal("0")

    @staticmethod
    def _build_metadata(now: datetime, *, remaining_credits: Decimal | None = None) -> dict:
        metadata = {
            "triggered_at": now.isoformat(),
        }
        if remaining_credits is not None:
            try:
                metadata["daily_credit_remaining"] = float(remaining_credits)
            except (TypeError, InvalidOperation):
                logger.debug("Unable to serialize daily credit remaining value for proactive trigger metadata.")
        return metadata

    @classmethod
    def _enqueue_analytics_event(cls, agent: PersistentAgent, metadata: dict) -> None:
        """Record an analytics event after the proactive trigger transaction commits."""
        try:
            trigger_mode = "forced" if metadata.get("force_trigger") else "scheduled"
            daily_remaining = metadata.get("daily_credit_remaining")
            properties = {
                "agent_id": str(agent.id),
                "agent_name": agent.name,
                "trigger_mode": trigger_mode,
                "triggered_at": metadata.get("triggered_at"),
                "proactive_min_interval_minutes": agent.proactive_min_interval_minutes,
                "proactive_max_daily": agent.proactive_max_daily,
                "daily_credit_limit": agent.daily_credit_limit,
            }
            if metadata.get("force_trigger") is not None:
                properties["force_trigger"] = bool(metadata.get("force_trigger"))
            if daily_remaining is not None:
                properties["daily_credit_remaining"] = daily_remaining
            if metadata.get("initiated_by"):
                properties["initiated_by"] = metadata["initiated_by"]
            if metadata.get("force_reason"):
                properties["force_reason"] = metadata["force_reason"]

            def _track():
                Analytics.track_event(
                    user_id=agent.user_id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_PROACTIVE_TRIGGERED,
                    source=AnalyticsSource.AGENT,
                    properties=properties,
                )

            transaction.on_commit(_track)
        except Exception:
            logger.exception("Failed to enqueue analytics event for proactive trigger agent %s", agent.id)

    @classmethod
    def _record_trigger(cls, agent: PersistentAgent, now: datetime, metadata: dict) -> ProactiveTriggerResult:
        """Persist system step and update agent state."""
        description = metadata.get("summary") or "Proactive outreach trigger recorded."

        with transaction.atomic():
            step = PersistentAgentStep.objects.create(
                agent=agent,
                description=f"Proactive trigger: {description}",
            )
            PersistentAgentSystemStep.objects.create(
                step=step,
                code=PersistentAgentSystemStep.Code.PROACTIVE_TRIGGER,
                notes=json.dumps(metadata),
            )
            PersistentAgent.objects.filter(pk=agent.pk).update(proactive_last_trigger_at=now)

        # Refresh agent instance so callers get updated timestamp
        agent.proactive_last_trigger_at = now
        cls._enqueue_analytics_event(agent, metadata)
        return ProactiveTriggerResult(agent=agent, step=step, metadata=metadata)

    @classmethod
    def force_trigger(
        cls,
        agent: PersistentAgent,
        *,
        initiated_by: str | None = None,
        reason: str | None = None,
    ) -> ProactiveTriggerResult:
        """Trigger proactive outreach for an agent without cooldown checks."""
        now = timezone.now()
        remaining = cls._daily_credit_remaining(agent)
        metadata = cls._build_metadata(now, remaining_credits=remaining)
        metadata["force_trigger"] = True
        if initiated_by:
            metadata["initiated_by"] = initiated_by
        if reason:
            metadata["force_reason"] = reason[:512]

        return cls._record_trigger(agent, now, metadata)

    @staticmethod
    def _get_redis_client():
        try:
            return get_redis_client()
        except Exception:
            logger.exception("Unable to fetch Redis client for proactive activation gates")
            return None

    @classmethod
    def _acquire_user_gate(cls, redis_client, user_id, min_interval_minutes: int) -> bool:
        """Ensure we have not recently triggered another agent for this user."""
        if redis_client is None:
            return True
        key = cls._user_gate_key(user_id)
        ttl_minutes = max(int(min_interval_minutes or 0), cls.USER_COOLDOWN_FALLBACK_MINUTES)
        ttl_seconds = ttl_minutes * 60
        try:
            acquired = redis_client.set(key, "1", ex=ttl_seconds, nx=True)
            return bool(acquired)
        except Exception:
            logger.exception("Redis gate check failed for user %s", user_id)
            return True

    @classmethod
    def _release_user_gate(cls, redis_client, user_id) -> None:
        if redis_client is None:
            return
        key = cls._user_gate_key(user_id)
        try:
            redis_client.delete(key)
        except Exception:
            logger.exception("Failed releasing proactive gate for user %s", user_id)

    @staticmethod
    def _user_gate_key(user_id) -> str:
        return f"proactive:user:{user_id}"

    @classmethod
    def _is_rollout_enabled_for_agent(cls, agent: PersistentAgent) -> bool:
        """Return True when the rollout flag permits proactive outreach for this agent."""
        if not agent.user_id:
            return False

        try:
            flag = get_waffle_flag_model().get(cls.ROLLOUT_FLAG_NAME)
        except Exception:
            logger.exception(
                "Failed loading waffle flag '%s' when evaluating rollout eligibility for agent %s",
                cls.ROLLOUT_FLAG_NAME,
                agent.id,
            )
            return False

        try:
            is_enabled = flag.is_active_for_user(agent.user)
        except Exception:
            logger.exception(
                "Error while evaluating waffle flag '%s' for user %s (agent %s)",
                cls.ROLLOUT_FLAG_NAME,
                agent.user_id,
                agent.id,
            )
            return False

        return bool(is_enabled)
