from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0325_merge_20260324_0243"),
    ]

    operations = [
        migrations.AddField(
            model_name="decodoipblock",
            name="proxy_type",
            field=models.CharField(
                choices=[
                    ("HTTP", "HTTP"),
                    ("HTTPS", "HTTPS"),
                    ("SOCKS4", "SOCKS4"),
                    ("SOCKS5", "SOCKS5"),
                ],
                default="SOCKS5",
                help_text="Proxy protocol used by this Decodo block.",
                max_length=8,
            ),
        ),
    ]
