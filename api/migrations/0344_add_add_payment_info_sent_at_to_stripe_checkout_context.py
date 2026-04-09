from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0343_merge_20260408_1831"),
    ]

    operations = [
        migrations.AddField(
            model_name="stripecheckoutcontext",
            name="add_payment_info_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
