import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from api.maintenance.prompt_archives import prune_prompt_archives_for_cutoff

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete persistent agent prompt archives older than the retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Retention window in days. Defaults to PROMPT_ARCHIVE_RETENTION_DAYS.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report the number of archives that would be deleted.",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=500,
            help="Number of rows to iterate per batch when pruning.",
        )

    def handle(self, *args, **options):
        retention_days = options["days"]
        dry_run = options["dry_run"]
        chunk_size = options["chunk_size"]

        if retention_days is None:
            retention_days = settings.PROMPT_ARCHIVE_RETENTION_DAYS

        if retention_days < 0:
            msg = f"Retention is negative ({retention_days}); skipping prune."
            self.stdout.write(self.style.WARNING(msg))
            logger.info(msg)
            return

        cutoff = timezone.now() - timedelta(days=retention_days)
        found, deleted = prune_prompt_archives_for_cutoff(
            cutoff,
            dry_run=dry_run,
            chunk_size=chunk_size,
        )

        if dry_run:
            summary = (
                f"[DRY RUN] Prompt archive prune completed. "
                f"{found} archives older than {retention_days} day(s) identified."
            )
            self.stdout.write(summary)
        else:
            summary = (
                f"Prompt archive prune completed. "
                f"{found} archives inspected, {deleted} deleted."
            )
            self.stdout.write(self.style.SUCCESS(summary))
        logger.info(
            "Prompt archive prune finished: dry_run=%s found=%s deleted=%s cutoff=%s chunk_size=%s",
            dry_run,
            found,
            deleted,
            cutoff,
            chunk_size,
        )
