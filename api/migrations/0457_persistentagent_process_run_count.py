from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0456_add_pipedream_apollo_guard_switch"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="process_run_count",
            field=models.PositiveBigIntegerField(
                blank=True,
                editable=False,
                help_text=(
                    "Number of recorded PROCESS_EVENTS runs; null until lazily initialized for legacy agents."
                ),
                null=True,
            ),
        ),
    ]
