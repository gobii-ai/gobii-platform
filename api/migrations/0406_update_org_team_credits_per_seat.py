from django.db import migrations


ORG_TEAM_PLAN_CODE = "org_team"
CREDITS_PER_SEAT_KEY = "credits_per_seat"
NEW_CREDITS_PER_SEAT = 1000
OLD_CREDITS_PER_SEAT = 500


def _set_org_team_credits_per_seat(apps, schema_editor, credits_per_seat):
    db_alias = schema_editor.connection.alias
    EntitlementDefinition = apps.get_model("api", "EntitlementDefinition")
    PlanVersion = apps.get_model("api", "PlanVersion")
    PlanVersionEntitlement = apps.get_model("api", "PlanVersionEntitlement")

    entitlement, _ = EntitlementDefinition.objects.using(db_alias).get_or_create(
        key=CREDITS_PER_SEAT_KEY,
        defaults={
            "display_name": "Credits per seat",
            "description": "Included monthly task credits granted per organization seat.",
            "value_type": "int",
            "unit": "credits",
        },
    )

    plan_versions = PlanVersion.objects.using(db_alias).filter(
        legacy_plan_code=ORG_TEAM_PLAN_CODE,
    )
    for plan_version in plan_versions.iterator():
        PlanVersionEntitlement.objects.using(db_alias).update_or_create(
            plan_version=plan_version,
            entitlement=entitlement,
            defaults={
                "value_int": credits_per_seat,
                "value_decimal": None,
                "value_bool": None,
                "value_text": None,
                "value_json": None,
                "currency": None,
            },
        )


def update_org_team_credits_per_seat(apps, schema_editor):
    _set_org_team_credits_per_seat(apps, schema_editor, NEW_CREDITS_PER_SEAT)


def restore_org_team_credits_per_seat(apps, schema_editor):
    _set_org_team_credits_per_seat(apps, schema_editor, OLD_CREDITS_PER_SEAT)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0405_update_brightdata_mcp_version"),
    ]

    operations = [
        migrations.RunPython(
            update_org_team_credits_per_seat,
            restore_org_team_credits_per_seat,
        ),
    ]
