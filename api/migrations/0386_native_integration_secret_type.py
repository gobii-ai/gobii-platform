from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0385_unique_public_template_slugs"),
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
        migrations.AlterField(
            model_name="persistentagentsecret",
            name="secret_type",
            field=models.CharField(
                choices=[
                    ("credential", "Credential"),
                    ("env_var", "Environment Variable"),
                    ("integration", "Integration"),
                ],
                default="credential",
                help_text=(
                    "Secret behavior type: credential (domain-scoped), env_var "
                    "(global sandbox env), or integration (hidden native app auth)."
                ),
                max_length=16,
            ),
        ),
    ]
