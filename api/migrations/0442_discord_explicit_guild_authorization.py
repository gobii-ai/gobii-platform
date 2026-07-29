from django.db import migrations, models


LEGACY_DISCOVERED = "legacy_discovered"
EXPLICIT_OAUTH = "explicit_oauth"


def classify_existing_discord_guild_claims(apps, schema_editor):
    discord_guild = apps.get_model("api", "PersistentAgentDiscordGuild")
    oauth_session = apps.get_model("api", "PersistentAgentDiscordOAuthSession")
    channel_subscription = apps.get_model("api", "PersistentAgentDiscordChannelSubscription")

    explicit_guild_ids = set(
        channel_subscription.objects.exclude(status="disabled").values_list("guild_id", flat=True)
    )
    for claim in discord_guild.objects.filter(is_active=True).iterator():
        owner_filter = {"organization_id": claim.organization_id}
        if claim.organization_id is None:
            owner_filter = {"owner_user_id": claim.owner_user_id, "organization_id__isnull": True}
        if oauth_session.objects.filter(
            completed_at__isnull=False,
            selected_guild_id=claim.guild_id,
            **owner_filter,
        ).exists():
            explicit_guild_ids.add(claim.id)

    discord_guild.objects.filter(id__in=explicit_guild_ids).update(
        authorization_source=EXPLICIT_OAUTH
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0441_disable_unseen_web_chat_followups"),
    ]

    operations = [
        migrations.AddField(
            model_name="persistentagentdiscordoauthsession",
            name="requested_guild_id",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="persistentagentdiscordguild",
            name="authorization_source",
            field=models.CharField(
                choices=[
                    ("legacy_discovered", "Legacy discovered"),
                    ("explicit_oauth", "Explicit OAuth"),
                ],
                db_index=True,
                default=LEGACY_DISCOVERED,
                max_length=32,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(
            classify_existing_discord_guild_claims,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="persistentagentdiscordguild",
            name="authorization_source",
            field=models.CharField(
                choices=[
                    ("legacy_discovered", "Legacy discovered"),
                    ("explicit_oauth", "Explicit OAuth"),
                ],
                db_index=True,
                default=EXPLICIT_OAUTH,
                max_length=32,
            ),
        ),
    ]
