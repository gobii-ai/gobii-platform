from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0371_merge_20260506_1404"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="persistentagenterror",
            index=models.Index(fields=["-created_at"], name="pa_error_created_idx"),
        ),
    ]
