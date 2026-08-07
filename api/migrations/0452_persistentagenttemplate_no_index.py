from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0451_disable_pipedream_google_sheets_guard_by_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="no_index",
            field=models.BooleanField(
                default=False,
                help_text="Add a noindex directive to the public detail page and exclude it from the sitemap.",
                verbose_name="No Index",
            ),
        ),
    ]
