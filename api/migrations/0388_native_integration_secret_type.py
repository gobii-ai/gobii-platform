from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0387_add_stripe_checkout_tos_consent_switch"),
    ]

    operations = [
        migrations.AlterField(
            model_name="globalsecret",
            name="secret_type",
            field=models.CharField(
                choices=[
                    ("credential", "Credential"),
                    ("env_var", "Environment Variable"),
                    ("integration", "Integration"),
                ],
                default="credential",
                max_length=16,
            ),
        ),
    ]
