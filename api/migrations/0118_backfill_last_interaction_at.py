from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0117_alter_persistentagent_last_interaction_at"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "UPDATE api_persistentagent "
                "SET last_interaction_at = COALESCE(last_interaction_at, created_at) "
                "WHERE last_interaction_at IS NULL;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        )
    ]

