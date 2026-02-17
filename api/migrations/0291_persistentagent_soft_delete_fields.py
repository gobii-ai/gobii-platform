from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0290_persistentagenttemplatelike"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="is_deleted",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="persistentagent",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RemoveConstraint(
            model_name="persistentagent",
            name="unique_persistent_agent_user_name",
        ),
        migrations.RemoveConstraint(
            model_name="persistentagent",
            name="unique_persistent_agent_org_name",
        ),
        migrations.AddConstraint(
            model_name="persistentagent",
            constraint=models.UniqueConstraint(
                condition=models.Q(organization__isnull=True, is_deleted=False),
                fields=("user", "name"),
                name="unique_persistent_agent_user_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="persistentagent",
            constraint=models.UniqueConstraint(
                condition=models.Q(organization__isnull=False, is_deleted=False),
                fields=("organization", "name"),
                name="unique_persistent_agent_org_name",
            ),
        ),
    ]
