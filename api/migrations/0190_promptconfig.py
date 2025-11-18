from django.db import migrations, models
import django.core.validators

from api.services.prompt_settings import (
    DEFAULT_PREMIUM_MESSAGE_HISTORY_LIMIT,
    DEFAULT_PREMIUM_PROMPT_TOKEN_BUDGET,
    DEFAULT_PREMIUM_TOOL_CALL_HISTORY_LIMIT,
    DEFAULT_STANDARD_MESSAGE_HISTORY_LIMIT,
    DEFAULT_STANDARD_PROMPT_TOKEN_BUDGET,
    DEFAULT_STANDARD_TOOL_CALL_HISTORY_LIMIT,
)


def seed_prompt_config(apps, schema_editor):
    PromptConfig = apps.get_model("api", "PromptConfig")
    if PromptConfig.objects.exists():
        return

    PromptConfig.objects.create(
        singleton_id=1,
        standard_prompt_token_budget=DEFAULT_STANDARD_PROMPT_TOKEN_BUDGET,
        premium_prompt_token_budget=DEFAULT_PREMIUM_PROMPT_TOKEN_BUDGET,
        standard_message_history_limit=DEFAULT_STANDARD_MESSAGE_HISTORY_LIMIT,
        premium_message_history_limit=DEFAULT_PREMIUM_MESSAGE_HISTORY_LIMIT,
        standard_tool_call_history_limit=DEFAULT_STANDARD_TOOL_CALL_HISTORY_LIMIT,
        premium_tool_call_history_limit=DEFAULT_PREMIUM_TOOL_CALL_HISTORY_LIMIT,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0189_merge_20251118_0151"),
    ]

    operations = [
        migrations.CreateModel(
            name="PromptConfig",
            fields=[
                (
                    "singleton_id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "standard_prompt_token_budget",
                    models.PositiveIntegerField(
                        default=120000,
                        help_text="Token budget applied when rendering prompts for standard tier agents.",
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                (
                    "premium_prompt_token_budget",
                    models.PositiveIntegerField(
                        default=120000,
                        help_text="Token budget applied when rendering prompts for premium tier agents.",
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                (
                    "standard_message_history_limit",
                    models.PositiveSmallIntegerField(
                        default=15,
                        help_text="Number of recent messages included for standard tier agents.",
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                (
                    "premium_message_history_limit",
                    models.PositiveSmallIntegerField(
                        default=20,
                        help_text="Number of recent messages included for premium tier agents.",
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                (
                    "standard_tool_call_history_limit",
                    models.PositiveSmallIntegerField(
                        default=15,
                        help_text="Number of recent tool calls included for standard tier agents.",
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                (
                    "premium_tool_call_history_limit",
                    models.PositiveSmallIntegerField(
                        default=20,
                        help_text="Number of recent tool calls included for premium tier agents.",
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Prompt configuration",
                "verbose_name_plural": "Prompt configuration",
            },
        ),
        migrations.RunPython(seed_prompt_config, migrations.RunPython.noop),
    ]
