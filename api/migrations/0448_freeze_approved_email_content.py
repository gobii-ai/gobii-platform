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
        migrations.AddConstraint(
            model_name="outboundemailreview",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("rendered_includes_throttle_footer", True),
                    ("status", "pending"),
                ),
                fields=("agent",),
                name="outbox_one_pending_throttle_footer",
            ),
        ),
    ]
