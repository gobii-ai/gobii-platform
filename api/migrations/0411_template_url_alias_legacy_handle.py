from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0410_migrate_meta_ads_profiles_to_native_integration"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagenttemplateurlalias",
            name="handle",
            field=models.SlugField(
                blank=True,
                default="",
                help_text="Legacy public profile handle used in the old template URL.",
                max_length=32,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="persistentagenttemplateurlalias",
            name="public_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="template_url_aliases",
                to="api.publicprofile",
            ),
        ),
        # Install indexes before the separate data migration. PostgreSQL
        # cannot create them after updates have queued foreign-key trigger
        # events in the same atomic migration.
        migrations.AddConstraint(
            model_name="persistentagenttemplateurlalias",
            constraint=models.UniqueConstraint(
                condition=~models.Q(handle=""),
                fields=("handle", "slug"),
                name="unique_public_template_url_alias_handle",
            ),
        ),
    ]
