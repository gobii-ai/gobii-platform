from django.db import migrations


DESCRIPTION_UPDATES = {
    "real-estate-research-analyst": (
        "An always-on pretrained worker that monitors real estate listings, researches comparable properties,"
        " analyzes market data, and compiles reports on property values and investment opportunities.",
        "An always-on pretrained AI employee that monitors real estate listings, researches comparable properties,"
        " analyzes market data, and compiles reports on property values and investment opportunities.",
    ),
    "project-manager": (
        "An always-on pretrained worker that coordinates project activities, tracks progress against milestones,"
        " manages task dependencies, identifies blockers, and keeps stakeholders informed with status updates and reports.",
        "An always-on pretrained AI employee that coordinates project activities, tracks progress against milestones,"
        " manages task dependencies, identifies blockers, and keeps stakeholders informed with status updates and reports.",
    ),
}


def update_descriptions(apps, schema_editor):
    PersistentAgentTemplate = apps.get_model("api", "PersistentAgentTemplate")
    for code, (old_description, new_description) in DESCRIPTION_UPDATES.items():
        PersistentAgentTemplate.objects.filter(
            code=code,
            description=old_description,
        ).update(description=new_description)


def restore_descriptions(apps, schema_editor):
    PersistentAgentTemplate = apps.get_model("api", "PersistentAgentTemplate")
    for code, (old_description, new_description) in DESCRIPTION_UPDATES.items():
        PersistentAgentTemplate.objects.filter(
            code=code,
            description=new_description,
        ).update(description=old_description)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0421_migrate_brightdata_base_tools_to_builtin"),
    ]

    operations = [
        migrations.RunPython(update_descriptions, restore_descriptions),
    ]
