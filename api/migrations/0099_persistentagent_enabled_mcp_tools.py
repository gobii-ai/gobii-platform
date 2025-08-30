# Generated manually for adding enabled_mcp_tools field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0098_add_waffle_flags'),
    ]

    operations = [
        migrations.AddField(
            model_name='persistentagent',
            name='enabled_mcp_tools',
            field=models.JSONField(
                default=list,
                blank=True,
                help_text='List of enabled MCP tool names for this agent (e.g., ["mcp_brightdata_search_engine"])'
            ),
        ),
    ]