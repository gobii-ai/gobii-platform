from django.db import migrations


def backfill_organization_billing(apps, schema_editor):
    Organization = apps.get_model("api", "Organization")
    OrganizationBilling = apps.get_model("api", "OrganizationBilling")

    from django.utils import timezone

    billing_day = timezone.now().day

    for org_id in (
        Organization.objects
        .filter(billing__isnull=True)
        .values_list("id", flat=True)
        .iterator()
    ):
        OrganizationBilling.objects.get_or_create(
            organization_id=org_id,
            defaults={"billing_cycle_anchor": billing_day},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0134_organizationbilling"),
    ]

    operations = [
        migrations.RunPython(backfill_organization_billing, migrations.RunPython.noop),
    ]
