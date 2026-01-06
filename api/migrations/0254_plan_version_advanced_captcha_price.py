from django.db import migrations, models

from constants.plans import PLAN_SLUG_BY_LEGACY_CODE, PlanNames

ADVANCED_CAPTCHA_PRICE_IDS = {
    PlanNames.STARTUP: "price_1Smb4bH5CITCWQWNeNXEH24M",
    PlanNames.SCALE: "price_1Smb51H5CITCWQWNgAPpF81o",
    PlanNames.ORG_TEAM: "price_1Smb4bH5CITCWQWNeNXEH24M",
}
ADVANCED_CAPTCHA_PRODUCT_ID = "prod_Tk5FIxl1f1ufEo"


def add_advanced_captcha_plan_prices(apps, schema_editor) -> None:
    PlanVersion = apps.get_model("api", "PlanVersion")
    PlanVersionPrice = apps.get_model("api", "PlanVersionPrice")

    for legacy_code, price_id in ADVANCED_CAPTCHA_PRICE_IDS.items():
        slug = PLAN_SLUG_BY_LEGACY_CODE.get(legacy_code, legacy_code)
        version = PlanVersion.objects.filter(plan__slug=slug, version_code="v1").first()
        if not version:
            continue

        PlanVersionPrice.objects.update_or_create(
            price_id=price_id,
            defaults={
                "plan_version": version,
                "kind": "advanced_captcha_resolution",
                "billing_interval": None,
                "product_id": ADVANCED_CAPTCHA_PRODUCT_ID,
            },
        )


def noop_reverse(apps, schema_editor) -> None:
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0253_addon_entitlement_advanced_captcha_resolution_delta"),
    ]

    operations = [
        migrations.AlterField(
            model_name="planversionprice",
            name="kind",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("base", "Base"),
                    ("seat", "Seat"),
                    ("overage", "Overage"),
                    ("task_pack", "Task pack"),
                    ("contact_pack", "Contact pack"),
                    ("browser_task_limit", "Browser task limit"),
                    ("advanced_captcha_resolution", "Advanced captcha resolution"),
                    ("dedicated_ip", "Dedicated IP"),
                ],
            ),
        ),
        migrations.RunPython(add_advanced_captcha_plan_prices, reverse_code=noop_reverse),
    ]
