from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0236_toolconfig_brightdata_amazon_product_search_limit"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagent",
            name="avatar",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to="agent_avatars/%Y/%m/%d/",
                help_text="Optional avatar image displayed for this agent.",
            ),
        ),
    ]
