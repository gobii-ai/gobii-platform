from django.db import migrations


def add_waffle_entries(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    Switch = apps.get_model("waffle", "Switch")

    # Ensure the feature flag exists with required defaults if missing
    if not Flag.objects.filter(name="multiplayer_agents").exists():
        Flag.objects.create(
            name="multiplayer_agents",
            # "Everyone" Unknown
            everyone=None,
            # Explicit rollout percent 0 (no random rollout)
            percent=0,
            # Access rules per requirements
            superusers=True,
            staff=False,
            authenticated=False,
        )

    # Ensure the switch exists and is NOT active if missing
    if not Switch.objects.filter(name="multisend_enabled").exists():
        Switch.objects.create(
            name="multisend_enabled",
            active=False,
        )


def noop(apps, schema_editor):
    # Do not remove existing flags/switches on reverse
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0097_commsallowlistentry_and_more"),

        # In case the waffle app was added after this migration, or we are in a test environment where this would not
        # have been created yet, depend on the initial migration of waffle. This ensures the Flag and Switch models exist.
        # If waffle is already present, this has no effect.
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_waffle_entries, noop),
    ]

