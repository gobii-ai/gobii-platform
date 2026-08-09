"""Celery task for persistent-agent step and communications compaction."""

import logging
from functools import partial

from celery import shared_task
from django.db import DatabaseError

from api.agent.core.compaction import ensure_comms_compacted, llm_summarise_comms
from api.agent.core.compaction_exceptions import CompactionSummaryError
from api.agent.core.history_compaction import (
    refresh_history_compaction_lease,
    release_history_compaction_lease,
)
from api.agent.core.step_compaction import ensure_steps_compacted, llm_summarise_steps
from api.models import LLMRoutingProfile, PersistentAgent

logger = logging.getLogger(__name__)

_RETRY_DELAYS_SECONDS = (5, 20)


@shared_task(
    bind=True,
    name="api.agent.tasks.compact_agent_history",
    max_retries=len(_RETRY_DELAYS_SECONDS),
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def compact_agent_history_task(
    self,
    persistent_agent_id: str,
    lease_token: str = "",
    routing_profile_id: str | None = None,
    eval_run_id: str | None = None,
) -> None:
    """Compact both durable history streams outside interactive processing."""
    retrying = False
    try:
        lease_owned = refresh_history_compaction_lease(persistent_agent_id, lease_token)
        if lease_owned is False:
            logger.info(
                "Skipping stale history compaction task for agent %s",
                persistent_agent_id,
            )
            return

        agent = (
            PersistentAgent.objects.alive()
            .select_related("user")
            .filter(id=persistent_agent_id, is_active=True)
            .first()
        )
        if agent is None:
            logger.info(
                "Skipping history compaction for missing or inactive agent %s",
                persistent_agent_id,
            )
            return

        routing_profile = None
        if routing_profile_id:
            routing_profile = LLMRoutingProfile.objects.filter(id=routing_profile_id).first()
            if routing_profile is None:
                raise CompactionSummaryError(
                    f"Routing profile {routing_profile_id} is unavailable"
                )

        summarizer_kwargs = {
            "agent": agent,
            "routing_profile": routing_profile,
            "eval_run_id": eval_run_id,
        }
        safety_identifier = agent.user_id
        ensure_steps_compacted(
            agent=agent,
            summarise_fn=partial(llm_summarise_steps, **summarizer_kwargs),
            safety_identifier=safety_identifier,
        )
        ensure_comms_compacted(
            agent=agent,
            summarise_fn=partial(llm_summarise_comms, **summarizer_kwargs),
            safety_identifier=safety_identifier,
        )
    except (CompactionSummaryError, DatabaseError) as exc:
        retries = int(self.request.retries or 0)
        if retries < self.max_retries:
            retrying = True
            refresh_history_compaction_lease(persistent_agent_id, lease_token)
            raise self.retry(exc=exc, countdown=_RETRY_DELAYS_SECONDS[retries])
        logger.warning(
            "History compaction exhausted retries for agent %s",
            persistent_agent_id,
            exc_info=True,
        )
    finally:
        if not retrying:
            release_history_compaction_lease(persistent_agent_id, lease_token)


__all__ = ["compact_agent_history_task"]
