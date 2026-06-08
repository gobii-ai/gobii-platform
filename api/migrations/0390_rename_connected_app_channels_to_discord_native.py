from django.db import migrations


OLD_SKILL_KEY = "connected_app_channels"
NEW_SKILL_KEY = "discord_native"


def migrate_discord_native_skill_state(apps, schema_editor):
    SystemSkillState = apps.get_model("api", "PersistentAgentSystemSkillState")

    old_states = list(
        SystemSkillState.objects.filter(skill_key=OLD_SKILL_KEY).only(
            "id",
            "agent_id",
            "skill_key",
            "is_enabled",
            "last_used_at",
            "usage_count",
        )
    )
    for old_state in old_states:
        new_state = (
            SystemSkillState.objects.filter(
                agent_id=old_state.agent_id,
                skill_key=NEW_SKILL_KEY,
            )
            .only("id", "is_enabled", "last_used_at", "usage_count")
            .first()
        )
        if new_state is None:
            old_state.skill_key = NEW_SKILL_KEY
            old_state.save(update_fields=["skill_key"])
            continue

        new_state.is_enabled = bool(new_state.is_enabled or old_state.is_enabled)
        new_state.usage_count = int(new_state.usage_count or 0) + int(old_state.usage_count or 0)
        if old_state.last_used_at and (
            new_state.last_used_at is None or old_state.last_used_at > new_state.last_used_at
        ):
            new_state.last_used_at = old_state.last_used_at
        new_state.save(update_fields=["is_enabled", "usage_count", "last_used_at"])
        old_state.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0389_browseruseagenttask_filespace_artifacts"),
    ]

    operations = [
        migrations.RunPython(migrate_discord_native_skill_state, migrations.RunPython.noop),
    ]
