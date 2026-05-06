from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0370_add_pricing_free_oss_plan_flag"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="persistentagenterror",
            index=models.Index(fields=["-created_at"], name="pa_error_created_idx"),
        ),
    ]
