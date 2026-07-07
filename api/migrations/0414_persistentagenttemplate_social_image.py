from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0413_persistentagenttemplate_omit_ai_title_suffix"),
    ]

    operations = [
        migrations.AddField(
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
                upload_to="public_template_social_images/%Y/%m/%d/",
            ),
        ),
    ]
