import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Tuple
from uuid import UUID, uuid4

import zstandard as zstd
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from api.models import PersistentAgent, PersistentAgentError, PersistentAgentPromptArchive
from api.services.agent_error_logging import log_agent_error

logger = logging.getLogger(__name__)


def archive_agent_prompt(
    *,
    agent: PersistentAgent,
    system_prompt: str,
    user_prompt: str,
    tokens_before: int,
    tokens_after: int,
    tokens_saved: int,
    token_budget: int,
    extra_payload: dict[str, Any] | None = None,
) -> Tuple[Optional[str], Optional[int], Optional[int], Optional[UUID]]:
    """Compress and persist a prompt payload without interrupting agent execution on failure."""
    timestamp = datetime.now(timezone.utc)
    archive_payload = {
        "agent_id": str(agent.id),
        "rendered_at": timestamp.isoformat(),
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "token_budget": token_budget,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "tokens_saved": tokens_saved,
        **(extra_payload or {}),
    }

    try:
        payload_bytes = json.dumps(archive_payload).encode("utf-8")
        compressed = zstd.ZstdCompressor(level=3).compress(payload_bytes)
        archive_key = (
            f"persistent_agents/{agent.id}/prompt_archives/"
            f"{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex}.json.zst"
        )
        default_storage.save(archive_key, ContentFile(compressed))
        try:
            archive = PersistentAgentPromptArchive.objects.create(
                agent=agent,
                rendered_at=timestamp,
                storage_key=archive_key,
                raw_bytes=len(payload_bytes),
                compressed_bytes=len(compressed),
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                tokens_saved=tokens_saved,
            )
        except Exception as exc:
            log_agent_error(
                agent,
                category=PersistentAgentError.Category.PROMPT_CONSTRUCTION,
                source="api.services.prompt_archives.archive_agent_prompt.metadata",
                message=f"Prompt archive metadata persistence failed for agent {agent.id}",
                exc=exc,
                logger=logger,
                context={
                    "archive_key": archive_key,
                    "raw_bytes": len(payload_bytes),
                    "compressed_bytes": len(compressed),
                    "tokens_before": tokens_before,
                    "tokens_after": tokens_after,
                    "tokens_saved": tokens_saved,
                },
            )
            try:
                default_storage.delete(archive_key)
            except Exception:
                logger.exception("Failed to delete orphaned prompt archive from storage: %s", archive_key)
            return archive_key, len(payload_bytes), len(compressed), None

        logger.info(
            "Archived prompt for agent %s: key=%s raw_bytes=%d compressed_bytes=%d",
            agent.id,
            archive_key,
            len(payload_bytes),
            len(compressed),
        )
        return archive_key, len(payload_bytes), len(compressed), archive.id
    except Exception as exc:
        log_agent_error(
            agent,
            category=PersistentAgentError.Category.PROMPT_CONSTRUCTION,
            source="api.services.prompt_archives.archive_agent_prompt",
            message=f"Prompt archive persistence failed for agent {agent.id}",
            exc=exc,
            logger=logger,
            context={
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "tokens_saved": tokens_saved,
            },
        )
        return None, None, None, None
