from django.db import migrations, models
import django.core.validators
from django.db.models import Q

from api.services.prompt_settings import (
    DEFAULT_MAX_MESSAGE_HISTORY_LIMIT,
    DEFAULT_MAX_PROMPT_TOKEN_BUDGET,
    DEFAULT_MAX_TOOL_CALL_HISTORY_LIMIT,
)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0190_promptconfig"),
    ]

    operations = [
        migrations.AddField(
            model_name="promptconfig",
            name="max_prompt_token_budget",
            field=models.PositiveIntegerField(
                default=DEFAULT_MAX_PROMPT_TOKEN_BUDGET,
                help_text="Token budget applied when rendering prompts for max tier agents.",
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AddField(
            model_name="promptconfig",
            name="max_message_history_limit",
            field=models.PositiveSmallIntegerField(
                default=DEFAULT_MAX_MESSAGE_HISTORY_LIMIT,
                help_text="Number of recent messages included for max tier agents.",
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AddField(
            model_name="promptconfig",
            name="max_tool_call_history_limit",
            field=models.PositiveSmallIntegerField(
                default=DEFAULT_MAX_TOOL_CALL_HISTORY_LIMIT,
                help_text="Number of recent tool calls included for max tier agents.",
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AddField(
            model_name="persistentllmtier",
            name="is_max",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Marks tiers reserved for max-tier routing.",
            ),
        ),
        migrations.AddField(
            model_name="persistenttierendpoint",
            name="is_max",
            field=models.BooleanField(
                db_index=True,
                default=False,
                editable=False,
                help_text="Matches the max-tier status of the associated tier.",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="persistentllmtier",
            unique_together={("token_range", "order", "is_premium", "is_max")},
        ),
        migrations.AddConstraint(
            model_name="persistentllmtier",
            constraint=models.CheckConstraint(
                check=~Q(is_max=True, is_premium=False),
                name="persistentllmtier_max_requires_premium",
            ),
        ),
    ]
