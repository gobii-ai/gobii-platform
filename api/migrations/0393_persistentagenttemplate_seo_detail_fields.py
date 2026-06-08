from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0392_merge_20260608_1640"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="seo_meta_description",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional complete meta description for the public template detail page.",
                max_length=200,
            ),
        ),
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="best_for",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional Markdown section describing ideal users, teams, or workflows.",
            ),
        ),
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="example_outputs",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional Markdown section describing outputs this template can produce.",
            ),
        ),
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="required_inputs",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional Markdown section describing inputs users should provide.",
            ),
        ),
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="how_it_works",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional Markdown section explaining the workflow this template follows.",
            ),
        ),
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="customization_notes",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional Markdown section with setup or customization guidance.",
            ),
        ),
        migrations.AddField(
            model_name="persistentagenttemplate",
            name="expected_tools_summary",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional Markdown section summarizing how the enabled tools are used.",
            ),
        ),
    ]
