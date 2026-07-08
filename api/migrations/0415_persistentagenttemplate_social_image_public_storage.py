from django.db import migrations, models

import api.storage_backends


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0414_persistentagenttemplate_social_image"),
    ]

    operations = [
        migrations.AlterField(
            model_name="persistentagenttemplate",
            name="social_image",
            field=models.ImageField(
                blank=True,
                help_text=(
                    "Optional uploaded Open Graph/Twitter preview image for the public template "
                    "detail page. Prefer 1200x630."
                ),
                max_length=512,
                null=True,
                storage=api.storage_backends.AliasedStorage(
                    "public_template_social_images"
                ),
                upload_to="public_template_social_images/%Y/%m/%d/",
            ),
        ),
    ]
