import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Sequence

from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from config.redis_client import get_redis_client
from api.models import (
    BrowserUseAgentTask,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentSecret,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)

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
            if not cls._recent_activity_cooldown_satisfied(agent, now):
                continue
            effective_min_interval = cls._effective_min_interval_minutes(agent)
            if not cls._min_interval_satisfied(agent, now, effective_min_interval):
                continue
            if not cls._acquire_user_gate(redis_client, agent.user_id, effective_min_interval):
                continue

            metadata = cls._build_metadata(agent, now)

            try:
                result = cls._record_trigger(agent, now, metadata)
            except Exception:
                logger.exception("Failed to record proactive trigger for agent %s", agent.id)
                cls._release_user_gate(redis_client, agent.user_id)
                continue

            triggered_results.append(result)
            seen_users.add(agent.user_id)

            cls._set_user_gate(redis_client, agent.user_id, effective_min_interval)

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
            .order_by("proactive_last_trigger_at", "last_interaction_at", "created_at")[: cls.SCAN_LIMIT]
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
    def _build_metadata(agent: PersistentAgent, now: datetime) -> dict:
        """Collect lightweight context to guide proactive outreach."""
        hints: List[str] = []

        recent_inbound = (
            PersistentAgentMessage.objects.filter(
                owner_agent=agent,
                is_outbound=False,
            )
            .order_by("-timestamp")
            .first()
        )

        recent_inbound_payload = None
        if recent_inbound:
            preview = (recent_inbound.body or "")[:160].replace("\n", " ")
            hints.append("Follow up on the most recent user message if it still needs action.")
            recent_inbound_payload = {
                "sender": getattr(recent_inbound.from_endpoint, "address", None),
                "timestamp": recent_inbound.timestamp.isoformat(),
                "preview": preview,
            }

        open_tasks_qs = BrowserUseAgentTask.objects.filter(
            agent=agent.browser_use_agent,
            status__in=[
                BrowserUseAgentTask.StatusChoices.PENDING,
                BrowserUseAgentTask.StatusChoices.IN_PROGRESS,
            ],
        ).order_by("-updated_at")[:5]
        open_tasks: List[dict] = [
            {
                "id": str(task.id),
                "status": task.status,
                "prompt": (task.prompt or "")[:120].replace("\n", " "),
            }
            for task in open_tasks_qs
        ]
        if open_tasks:
            hints.append("Review active web tasks and update the user on progress or next steps.")

        pending_secrets_qs = PersistentAgentSecret.objects.filter(agent=agent, requested=True).values_list("name", flat=True)
        pending_secrets = list(pending_secrets_qs[:5])
        if pending_secrets:
            hints.append("Remind the user about pending credential requests if they block progress.")

        summary_parts: List[str] = []
        if recent_inbound_payload:
            summary_parts.append("recent inbound message awaiting response")
        if open_tasks:
            summary_parts.append("active browser tasks in progress")
        if pending_secrets:
            summary_parts.append("credentials waiting on the user")
        summary = ", ".join(summary_parts) if summary_parts else "check in context and offer related help"

        metadata = {
            "triggered_at": now.isoformat(),
            "summary": summary,
            "hints": hints,
            "recent_inbound": recent_inbound_payload,
            "open_tasks": open_tasks,
            "pending_secrets": pending_secrets,
        }

        return metadata

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
        return ProactiveTriggerResult(agent=agent, step=step, metadata=metadata)

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
        try:
            exists = redis_client.exists(key)
            if exists:
                return False
            # Do not set the gate until after we successfully record the trigger
            return True
        except Exception:
            logger.exception("Redis gate check failed for user %s", user_id)
            return True

    @classmethod
    def _set_user_gate(cls, redis_client, user_id, min_interval_minutes: int) -> None:
        if redis_client is None:
            return
        key = cls._user_gate_key(user_id)
        ttl_minutes = max(int(min_interval_minutes or 0), cls.USER_COOLDOWN_FALLBACK_MINUTES)
        try:
            redis_client.set(key, "1", ex=ttl_minutes * 60)
        except Exception:
            logger.exception("Failed setting proactive gate for user %s", user_id)

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
