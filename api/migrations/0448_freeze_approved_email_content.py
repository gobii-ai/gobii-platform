from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0447_merge_20260731_1514"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboundemailreview",
            name="rendered_html_body",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="outboundemailreview",
            name="rendered_plaintext_body",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="outboundemailreview",
            name="rendered_includes_throttle_footer",
            field=models.BooleanField(default=False),
        ),
    ]
