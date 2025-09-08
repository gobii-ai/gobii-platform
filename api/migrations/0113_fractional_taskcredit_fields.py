from django.db import migrations, models
import django.db.models.expressions


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0112_browseruseagenttask_cached_tokens_and_more"),
    ]

    operations = [
        # Drop the generated column first to allow altering dependent column types in PostgreSQL
        migrations.RemoveField(
            model_name="taskcredit",
            name="available_credits",
        ),
        migrations.AlterField(
            model_name="taskcredit",
            name="credits",
            field=models.DecimalField(max_digits=12, decimal_places=3),
        ),
        migrations.AlterField(
            model_name="taskcredit",
            name="credits_used",
            field=models.DecimalField(max_digits=12, decimal_places=3, default=0),
        ),
        # Re-add the generated column with the updated Decimal output field
        migrations.AddField(
            model_name="taskcredit",
            name="available_credits",
            field=models.GeneratedField(
                db_persist=True,
                expression=django.db.models.expressions.CombinedExpression(
                    models.F("credits"), "-", models.F("credits_used")
                ),
                output_field=models.DecimalField(max_digits=12, decimal_places=3),
            ),
        ),
    ]
