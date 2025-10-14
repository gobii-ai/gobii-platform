import logging

logger = logging.getLogger(__name__)


def prune_prompt_archives_for_cutoff(cutoff, *, dry_run=False, chunk_size=500):
    """
    Delete prompt archive payloads rendered before the provided cutoff.

    Returns (found, deleted) counts to aid logging/metrics.
    """
    from api.models import PersistentAgentPromptArchive

    queryset = (
        PersistentAgentPromptArchive.objects.filter(rendered_at__lt=cutoff)
        .order_by("rendered_at")
    )

    found = 0
    deleted = 0

    for archive in queryset.iterator(chunk_size=chunk_size):
        found += 1
        if dry_run:
            continue
        try:
            archive.delete()
            deleted += 1
        except Exception:
            logger.exception("Failed to delete prompt archive %s", archive.id)

    return found, deleted
