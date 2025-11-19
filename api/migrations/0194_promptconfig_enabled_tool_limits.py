from django.db import migrations, models
import django.core.validators

from api.services.prompt_settings import (
    DEFAULT_MAX_ENABLED_TOOL_LIMIT,
    DEFAULT_PREMIUM_ENABLED_TOOL_LIMIT,
    DEFAULT_STANDARD_ENABLED_TOOL_LIMIT,
)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0193_merge_20251119_1614")
    ]

    operations = [
        migrations.AddField(
            model_name="promptconfig",
            name="standard_enabled_tool_limit",
            field=models.PositiveSmallIntegerField(
                default=DEFAULT_STANDARD_ENABLED_TOOL_LIMIT,
                help_text="Number of concurrently enabled tools allowed for standard tier agents.",
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AddField(
            model_name="promptconfig",
            name="premium_enabled_tool_limit",
            field=models.PositiveSmallIntegerField(
                default=DEFAULT_PREMIUM_ENABLED_TOOL_LIMIT,
                help_text="Number of concurrently enabled tools allowed for premium tier agents.",
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AddField(
            model_name="promptconfig",
            name="max_enabled_tool_limit",
            field=models.PositiveSmallIntegerField(
                default=DEFAULT_MAX_ENABLED_TOOL_LIMIT,
                help_text="Number of concurrently enabled tools allowed for max tier agents.",
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
    ]
