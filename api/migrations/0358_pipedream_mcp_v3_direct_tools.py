from django.db import migrations


def use_direct_pipedream_tools(apps, schema_editor):
    MCPServerConfig = apps.get_model("api", "MCPServerConfig")

    for cfg in MCPServerConfig.objects.filter(name="pipedream"):
        metadata = dict(cfg.metadata or {})
        metadata.pop("mode", None)
        update_fields = ["metadata"]
        cfg.metadata = metadata
        if cfg.url == "https://remote.mcp.pipedream.net":
            cfg.url = "https://remote.mcp.pipedream.net/v3"
            update_fields.append("url")
        cfg.save(update_fields=update_fields)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0357_add_user_fingerprint_server_event_id"),
    ]

    operations = [
        migrations.RunPython(use_direct_pipedream_tools, migrations.RunPython.noop),
    ]
