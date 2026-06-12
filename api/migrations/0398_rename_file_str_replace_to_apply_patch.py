from django.db import migrations
from django.db.models import Subquery


OLD_TOOL_NAME = "file_str_replace"
NEW_TOOL_NAME = "apply_patch"


def rename_file_str_replace_tool(apps, schema_editor):
    PersistentAgentEnabledTool = apps.get_model("api", "PersistentAgentEnabledTool")
    PersistentAgentSkill = apps.get_model("api", "PersistentAgentSkill")
    GlobalAgentSkill = apps.get_model("api", "GlobalAgentSkill")

    _rename_enabled_tool_rows(PersistentAgentEnabledTool, OLD_TOOL_NAME, NEW_TOOL_NAME)
    _replace_skill_tool_references(PersistentAgentSkill, OLD_TOOL_NAME, NEW_TOOL_NAME)
    _replace_skill_tool_references(GlobalAgentSkill, OLD_TOOL_NAME, NEW_TOOL_NAME)


def reverse_rename_file_str_replace_tool(apps, schema_editor):
    PersistentAgentEnabledTool = apps.get_model("api", "PersistentAgentEnabledTool")
    PersistentAgentSkill = apps.get_model("api", "PersistentAgentSkill")
    GlobalAgentSkill = apps.get_model("api", "GlobalAgentSkill")

    _rename_enabled_tool_rows(PersistentAgentEnabledTool, NEW_TOOL_NAME, OLD_TOOL_NAME)
    _replace_skill_tool_references(PersistentAgentSkill, NEW_TOOL_NAME, OLD_TOOL_NAME)
    _replace_skill_tool_references(GlobalAgentSkill, NEW_TOOL_NAME, OLD_TOOL_NAME)


def _rename_enabled_tool_rows(PersistentAgentEnabledTool, old_tool_name, new_tool_name):
    already_enabled_agent_ids = (
        PersistentAgentEnabledTool.objects
        .filter(tool_full_name=new_tool_name)
        .values("agent_id")
    )
    PersistentAgentEnabledTool.objects.filter(
        tool_full_name=old_tool_name,
        agent_id__in=Subquery(already_enabled_agent_ids),
    ).delete()
    PersistentAgentEnabledTool.objects.filter(tool_full_name=old_tool_name).update(
        tool_full_name=new_tool_name
    )
    PersistentAgentEnabledTool.objects.filter(tool_name=old_tool_name).update(
        tool_name=new_tool_name
    )


def _replace_skill_tool_references(SkillModel, old_tool_name, new_tool_name):
    for skill in SkillModel.objects.exclude(tools=[]).only("id", "tools").iterator():
        tools, changed = _replace_tool_name(skill.tools, old_tool_name, new_tool_name)
        if changed:
            skill.tools = tools
            skill.save(update_fields=["tools"])


def _replace_tool_name(tool_names, old_tool_name, new_tool_name):
    if not isinstance(tool_names, list) or old_tool_name not in tool_names:
        return tool_names, False

    already_has_new_tool = new_tool_name in tool_names
    changed = False
    updated_tool_names = []
    for tool_name in tool_names:
        if tool_name == old_tool_name:
            changed = True
            if already_has_new_tool:
                continue
            updated_tool_names.append(new_tool_name)
            already_has_new_tool = True
            continue
        updated_tool_names.append(tool_name)
    return updated_tool_names, changed


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0397_rename_discord_send_message_tool"),
    ]

    operations = [
        migrations.RunPython(
            rename_file_str_replace_tool,
            reverse_rename_file_str_replace_tool,
        ),
    ]
