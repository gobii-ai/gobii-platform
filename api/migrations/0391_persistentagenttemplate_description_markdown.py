from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0390_persistentagenttemplate_is_official"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="description_markdown",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional rich Markdown body for the public template detail page.",
            ),
        ),
    ]
