from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0296_userattribution_reddit_click_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpserveroauthcredential",
            name="remote_auth_state_encrypted",
            field=models.BinaryField(blank=True, null=True),
        ),
    ]
