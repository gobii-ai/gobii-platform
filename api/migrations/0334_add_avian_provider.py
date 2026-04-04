"""Seed LLMProvider record for Avian (OpenAI-compatible inference API)."""

from django.db import migrations


def seed_avian_provider(apps, schema_editor):
    LLMProvider = apps.get_model("api", "LLMProvider")
    LLMProvider.objects.get_or_create(
        key="avian",
        defaults={
            "display_name": "Avian",
            "enabled": True,
            "env_var_name": "AVIAN_API_KEY",
            "browser_backend": "OPENAI_COMPAT",
            "supports_safety_identifier": False,
            "model_prefix": "openai/",
            "vertex_project": "",
            "vertex_location": "",
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0333_globalagentskill_and_agentskill_source"),
    ]

    operations = [
        migrations.RunPython(seed_avian_provider, reverse_code=migrations.RunPython.noop),
    ]
