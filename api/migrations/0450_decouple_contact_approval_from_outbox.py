from django.db import migrations, models


def disable_outbox_for_grandfathered_agents(apps, schema_editor):
    PersistentAgent = apps.get_model("api", "PersistentAgent")
    # Migration 0430 used review_new_contacts as a compatibility mirror for
    # legacy require_approval agents. Now that the controls are independent,
    # those mirrored values should preserve the pre-Outbox delivery behavior.
    # Staff users were the feature-flagged pilot cohort, while review history
    # is durable evidence that an agent already used the explicit policy.
    PersistentAgent.objects.filter(
        email_sending_mode="review_new_contacts",
        user__is_staff=False,
        user__is_superuser=False,
        outbound_email_reviews__isnull=True,
    ).update(email_sending_mode="send_automatically")


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0449_visible_agent_message_roster_index"),
    ]

    operations = [
        migrations.AlterField(
            model_name="persistentagent",
            name="email_sending_mode",
            field=models.CharField(
                choices=[
                    ("review_all_external", "Review before send"),
                    ("review_new_contacts", "Review only new contacts"),
                    ("send_automatically", "Send automatically"),
                ],
                default="send_automatically",
                help_text="Requested email sending mode. Organization policy may enforce a stricter effective mode.",
                max_length=32,
            ),
        ),
        migrations.RunPython(
            disable_outbox_for_grandfathered_agents,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
