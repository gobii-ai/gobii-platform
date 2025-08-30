from django.db import migrations

def backfill_billing_anchor(apps, schema_editor):
    UserBilling = apps.get_model("api", "UserBilling")

    # For each UserBilling instance, set the billing_cycle_anchor to the day of the month
    for billing in UserBilling.objects.select_related("user").all():
        billing.billing_cycle_anchor = billing.user.date_joined.day
        billing.save(update_fields=["billing_cycle_anchor"])

class Migration(migrations.Migration):

    dependencies = [
        ('api', '0074_userbilling_billing_cycle_anchor'),
    ]

    operations = [
        migrations.RunPython(backfill_billing_anchor, migrations.RunPython.noop),
    ]
