from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


def backfill_offpeak_burn_rate_threshold(apps, schema_editor):
    DailyCreditConfig = apps.get_model("api", "DailyCreditConfig")
    DailyCreditConfig.objects.filter(
        offpeak_burn_rate_threshold_per_hour__isnull=True,
    ).update(
        offpeak_burn_rate_threshold_per_hour=models.F("burn_rate_threshold_per_hour"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0306_persistentagent_avatar_generation_cooldown_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="dailycreditconfig",
            name="offpeak_burn_rate_threshold_per_hour",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=12,
                null=True,
                validators=[MinValueValidator(Decimal("0"))],
                help_text="Burn-rate threshold used during off-peak local hours (22:00-06:00).",
            ),
        ),
        migrations.RunPython(
            backfill_offpeak_burn_rate_threshold,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="dailycreditconfig",
            name="offpeak_burn_rate_threshold_per_hour",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("3"),
                max_digits=12,
                validators=[MinValueValidator(Decimal("0"))],
                help_text="Burn-rate threshold used during off-peak local hours (22:00-06:00).",
            ),
        ),
    ]
