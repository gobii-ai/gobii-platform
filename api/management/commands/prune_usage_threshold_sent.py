from datetime import timedelta
from dateutil.relativedelta import relativedelta     # pip install python-dateutil
from django.core.management.base import BaseCommand
from django.utils import timezone

from api.models import UsageThresholdSent

RETENTION_MONTHS = 18

class Command(BaseCommand):
    """
    Command to delete UsageThresholdSent rows older than a specified retention period.
    """

    help = (
        f"Delete UsageThresholdSent rows older than {RETENTION_MONTHS} months "
        "to keep the table lean."
    )

    def handle(self, *args, **options):
        cutoff_date = timezone.now() - relativedelta(months=RETENTION_MONTHS)
        cutoff_ym   = cutoff_date.strftime("%Y%m")   # e.g. '202401'

        deleted, _ = (
            UsageThresholdSent.objects
            .filter(period_ym__lt=cutoff_ym)
            .delete()
        )

        self.stdout.write(
            self.style.SUCCESS(f"Deleted {deleted} outdated UsageThresholdSent rows.")
        )
