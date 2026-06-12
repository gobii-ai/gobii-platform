import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0395_litellm_pricing_model_overrides"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttoolcall",
            name="parent_tool_call",
            field=models.ForeignKey(
                blank=True,
                help_text="Parent tool call that spawned this nested tool call, when applicable.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="child_tool_calls",
                to="api.persistentagenttoolcall",
            ),
        ),
    ]
