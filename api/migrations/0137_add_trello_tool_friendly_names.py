from django.db import migrations


def add_trello_friendly_names(apps, schema_editor):
    ToolFriendlyName = apps.get_model("api", "ToolFriendlyName")

    entries = {
        "trello-create-card": "Trello - Create Card",
        "trello-get-cards-on-board": "Trello - Get Cards On Board",
        "trello-get-cards-in-list": "Trello - Get Cards On List",
        "trello-update-card": "Trello - Update Card",
        "trello-search-cards": "Trello - Search Cards",
        "trello-find-list": "Trello - Find List",
        "trello-move-card-to-list": "Trello - Move Card To List",
        "trello-create-list": "Trello - Create List",
        "trello-archive-card": "Trello - Archive Card",
        "trello-add-member-to-card": "Trello - Add Member To Card",
    }

    for tool_name, display_name in entries.items():
        ToolFriendlyName.objects.get_or_create(
            tool_name=tool_name,
            defaults={"display_name": display_name},
        )


def remove_trello_friendly_names(apps, schema_editor):
    ToolFriendlyName = apps.get_model("api", "ToolFriendlyName")
    ToolFriendlyName.objects.filter(
        tool_name__in=[
            "trello-create-card",
            "trello-get-cards-on-board",
            "trello-get-cards-in-list",
            "trello-update-card",
            "trello-search-cards",
            "trello-find-list",
            "trello-move-card-to-list",
            "trello-create-list",
            "trello-archive-card",
            "trello-add-member-to-card",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0136_backfill_organizationbilling"),
    ]

    operations = [
        migrations.RunPython(add_trello_friendly_names, remove_trello_friendly_names),
    ]
