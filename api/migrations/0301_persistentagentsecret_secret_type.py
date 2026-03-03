from django.db import migrations, models


def backfill_secret_type(apps, schema_editor):
    PersistentAgentSecret = apps.get_model("api", "PersistentAgentSecret")
    PersistentAgentSecret.objects.filter(secret_type__isnull=True).update(secret_type="credential")


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0300_userquota_max_intelligence_tier"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentsecret",
            name="secret_type",
            field=models.CharField(
                choices=[("credential", "Credential"), ("env_var", "Environment Variable")],
                default="credential",
                help_text="Secret behavior type: credential (domain-scoped) or env_var (global sandbox env).",
                max_length=16,
            ),
        ),
        migrations.RunPython(backfill_secret_type, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="persistentagentsecret",
            name="unique_agent_domain_secret_name",
        ),
        migrations.RemoveConstraint(
            model_name="persistentagentsecret",
            name="unique_agent_domain_secret_key",
        ),
        migrations.AddConstraint(
            model_name="persistentagentsecret",
            constraint=models.UniqueConstraint(
                fields=("agent", "secret_type", "domain_pattern", "name"),
                name="unique_agent_type_domain_secret_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="persistentagentsecret",
            constraint=models.UniqueConstraint(
                fields=("agent", "secret_type", "domain_pattern", "key"),
                name="unique_agent_type_domain_secret_key",
            ),
        ),
        migrations.RemoveIndex(
            model_name="persistentagentsecret",
            name="pa_secret_agent_domain_idx",
        ),
        migrations.AddIndex(
            model_name="persistentagentsecret",
            index=models.Index(
                fields=["agent", "secret_type", "domain_pattern"],
                name="pa_secret_agent_type_dom_idx",
            ),
        ),
    ]
