from django.db import migrations


FLAG_NAMES = (
    "pricing_free_oss_plan",
    "persistent_agent_llm_judge",
    "persistent_agent_planning_mode",
    "personal_agent_signup_starter_charter",
    "fish_homepage",
    "fish_upper_left",
    "multiplayer_agents",
)

SWITCH_NAMES = (
    "fish_collateral",
)


def remove_obsolete_waffle_rows(apps, schema_editor):
    Flag = apps.get_model("waffle", "Flag")
    Switch = apps.get_model("waffle", "Switch")

    Flag.objects.filter(name__in=FLAG_NAMES).delete()
    Switch.objects.filter(name__in=SWITCH_NAMES).delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0406_persistentagentcompletion_time_to_first_token_ms"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(remove_obsolete_waffle_rows, noop),
    ]
