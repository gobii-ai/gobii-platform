from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0298_merge_20260301_1915"),
    ]

    operations = [
        migrations.AddField(
            model_name="promptconfig",
            name="browser_task_unified_history_limit",
            field=models.PositiveSmallIntegerField(
                default=20,
                help_text="Maximum number of completed browser tasks included in unified history.",
                validators=[MinValueValidator(1)],
            ),
        ),
    ]
