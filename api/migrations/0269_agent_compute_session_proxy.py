from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0268_agent_compute_session_sync_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentcomputesession",
            name="proxy_server",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="compute_sessions",
                to="api.proxyserver",
            ),
        ),
    ]
