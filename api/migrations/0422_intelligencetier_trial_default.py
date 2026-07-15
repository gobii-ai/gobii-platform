from django.db import migrations, models
from django.db.models import Q


def seed_trial_default_intelligence_tier(apps, schema_editor):
    IntelligenceTier = apps.get_model("api", "IntelligenceTier")
    if IntelligenceTier.objects.filter(is_trial_default=True).exists():
        return

    tier = IntelligenceTier.objects.filter(key="max").first()
    if tier is None:
        return

    tier.is_trial_default = True
    tier.save(update_fields=["is_trial_default"])


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0421_migrate_brightdata_base_tools_to_builtin"),
    ]

    operations = [
        migrations.AddField(
            model_name="intelligencetier",
            name="is_trial_default",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When enabled, this tier is used as the default for new agents owned by "
                    "active trials (clamped per plan)."
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name="intelligencetier",
            constraint=models.UniqueConstraint(
                fields=("is_trial_default",),
                condition=Q(is_trial_default=True),
                name="unique_trial_default_intelligence_tier",
            ),
        ),
        migrations.RunPython(seed_trial_default_intelligence_tier, migrations.RunPython.noop),
    ]
