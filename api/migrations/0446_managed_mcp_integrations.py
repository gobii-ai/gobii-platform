from django.db import migrations, models
from django.db.models import Q


HUBSPOT_MCP_FLAG = "hubspot_mcp"


def add_hubspot_mcp_flag(apps, schema_editor):
    flag_model = apps.get_model("waffle", "Flag")
    if flag_model.objects.filter(name=HUBSPOT_MCP_FLAG).exists():
        return
    flag_model.objects.create(
        name=HUBSPOT_MCP_FLAG,
        everyone=None,
        percent=0,
        superusers=True,
        staff=True,
        authenticated=False,
        note="Use Gobii's managed OAuth client with HubSpot's remote MCP server.",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0445_judge_lifecycle_and_email_bcc"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpserverconfig",
            name="managed_integration_key",
            field=models.SlugField(
                blank=True,
                default="",
                help_text="Product-managed integration owning this MCP configuration.",
                max_length=64,
            ),
        ),
        migrations.AddIndex(
            model_name="mcpserverconfig",
            index=models.Index(
                fields=["managed_integration_key", "is_active"],
                name="mcp_server_managed_active_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="mcpserverconfig",
            constraint=models.UniqueConstraint(
                condition=Q(scope="organization", managed_integration_key__gt=""),
                fields=("organization", "managed_integration_key"),
                name="unique_org_managed_mcp_integration",
            ),
        ),
        migrations.AddConstraint(
            model_name="mcpserverconfig",
            constraint=models.UniqueConstraint(
                condition=Q(scope="user", managed_integration_key__gt=""),
                fields=("user", "managed_integration_key"),
                name="unique_user_managed_mcp_integration",
            ),
        ),
        migrations.RunPython(add_hubspot_mcp_flag, migrations.RunPython.noop),
    ]
