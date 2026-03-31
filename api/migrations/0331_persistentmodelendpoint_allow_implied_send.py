from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0330_toolconfig_tool_search_auto_enable_apps"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentmodelendpoint",
            name="allow_implied_send",
            field=models.BooleanField(
                default=True,
                help_text="Controls whether plain text can auto-route to the active web chat recipient for this model.",
            ),
        ),
    ]
