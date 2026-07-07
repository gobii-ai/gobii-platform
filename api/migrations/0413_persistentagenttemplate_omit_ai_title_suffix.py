from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0412_alter_persistentagentsystemmessage_body_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="omit_ai_agent_template_title_suffix",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Use the display name alone for the public detail page SEO and social title "
                    "instead of appending 'AI Agent Template'."
                ),
            ),
        ),
    ]
