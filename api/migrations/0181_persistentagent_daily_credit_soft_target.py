from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations, models


def halve_daily_credit_limits(apps, schema_editor):
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    for agent in PersistentAgent.objects.filter(daily_credit_limit__isnull=False).iterator(chunk_size=500):
        raw_value = agent.daily_credit_limit
        try:
            decimal_value = raw_value if isinstance(raw_value, Decimal) else Decimal(raw_value)
        except Exception:
            continue
        new_value = (decimal_value / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        agent.daily_credit_limit = new_value
        agent.save(update_fields=["daily_credit_limit"])


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0180_persistentagentcompletion_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="persistentagent",
            name="daily_credit_limit",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Soft daily credit target; system enforces a hard stop at 2× this value. Null means unlimited.",
                max_digits=6,
                null=True,
            ),
        ),
        migrations.RunPython(halve_daily_credit_limits, migrations.RunPython.noop),
    ]
