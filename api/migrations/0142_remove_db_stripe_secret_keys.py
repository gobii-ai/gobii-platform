from django.db import migrations


def remove_stripe_secret_keys(apps, schema_editor):
    StripeConfigEntry = apps.get_model("api", "StripeConfigEntry")
    StripeConfigEntry.objects.filter(name__in=["live_secret_key", "test_secret_key"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0141_populate_stripe_config_from_env"),
    ]

    operations = [
        migrations.RunPython(remove_stripe_secret_keys, migrations.RunPython.noop),
    ]
