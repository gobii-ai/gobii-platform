"""Celery task for reported-message advisory judge runs."""

import logging

import litellm
from celery import shared_task

from api.agent.core.agent_judge import run_reported_agent_judge
from api.agent.core.llm_config import LLMNotConfiguredError
from api.agent.core.llm_utils import LiteLLMResponseError
from api.models import PersistentAgent, PersistentAgentMessage

logger = logging.getLogger(__name__)

REPORTED_JUDGE_EXPECTED_ERRORS = (
    LLMNotConfiguredError,
    LiteLLMResponseError,
    litellm.OpenAIError,
    OSError,
    TimeoutError,
)


@shared_task(bind=True, name="api.agent.tasks.run_reported_agent_judge")
def run_reported_agent_judge_task(self, agent_id: str, message_id: str, user_comment: str = "") -> None:  # noqa: ANN001
    try:
        agent = PersistentAgent.objects.get(id=agent_id)
        message = PersistentAgentMessage.objects.get(
            id=message_id,
            owner_agent=agent,
            is_outbound=True,
            peer_agent__isnull=True,
        )
    except (PersistentAgent.DoesNotExist, PersistentAgentMessage.DoesNotExist):
        logger.info("Skipping reported-message judge; agent/message no longer exists.")
        return

    try:
        run_reported_agent_judge(agent, reported_message=message, user_comment=user_comment)
    except REPORTED_JUDGE_EXPECTED_ERRORS as exc:
        logger.exception(
            "Reported-message judge failed for agent %s message %s.",
            agent.id,
            message.id,
        )
