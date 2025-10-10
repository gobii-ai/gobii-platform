from django.db import migrations


def mark_decodo_proxies_dedicated(apps, schema_editor):
    ProxyServer = apps.get_model("api", "ProxyServer")
    ProxyServer.objects.filter(decodo_ip__isnull=False).update(is_dedicated=True)


def unmark_decodo_proxies_dedicated(apps, schema_editor):
    ProxyServer = apps.get_model("api", "ProxyServer")
    ProxyServer.objects.filter(decodo_ip__isnull=False).update(is_dedicated=False)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0156_dedicatedproxyallocation_proxyserver_is_dedicated_and_more"),
    ]

    operations = [
        migrations.RunPython(
            mark_decodo_proxies_dedicated,
            reverse_code=unmark_decodo_proxies_dedicated,
        ),
    ]
