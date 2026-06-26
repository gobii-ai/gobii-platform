from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0402_remove_agent_retry_completion_on_web_session_activation_switch"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="persistentagent",
            name="agent_color",
        ),
        migrations.DeleteModel(
            name="AgentColor",
        ),
    ]
