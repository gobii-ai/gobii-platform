from django.core.management.base import BaseCommand, CommandError

from api.models import PersistentAgentDiscordChannelSubscription, PersistentAgentDiscordGuild
from api.services.discord_bot import DiscordBotIntegrationError, disconnect_discord_guild_claim


class Command(BaseCommand):
    help = "Report or remove Discord guild claims created by the legacy broad guild discovery flow."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Remove Discord-side access and deactivate eligible legacy claims.",
        )

    def handle(self, *args, **options):
        apply_cleanup = bool(options["apply"])
        candidates = (
            PersistentAgentDiscordGuild.objects.filter(
                is_active=True,
                authorization_source=PersistentAgentDiscordGuild.AuthorizationSource.LEGACY_DISCOVERED,
            )
            .exclude(
                channel_subscriptions__status__in=[
                    PersistentAgentDiscordChannelSubscription.Status.ACTIVE,
                    PersistentAgentDiscordChannelSubscription.Status.ERROR,
                ]
            )
            .distinct()
            .order_by("name", "guild_id")
        )
        candidate_count = candidates.count()
        mode = "apply" if apply_cleanup else "dry-run"
        self.stdout.write(f"Discord legacy cleanup ({mode}): {candidate_count} eligible guild(s).")

        removed = 0
        failed = 0
        for guild in candidates.iterator():
            owner_label = f"org:{guild.organization_id}" if guild.organization_id else f"user:{guild.owner_user_id}"
            if not apply_cleanup:
                self.stdout.write(f"WOULD_REMOVE {guild.guild_id} {owner_label} {guild.name}")
                continue
            try:
                disconnect_discord_guild_claim(guild)
            except DiscordBotIntegrationError as exc:
                failed += 1
                self.stderr.write(f"FAILED {guild.guild_id} {owner_label}: {exc}")
                continue
            removed += 1
            self.stdout.write(f"REMOVED {guild.guild_id} {owner_label} {guild.name}")

        if apply_cleanup:
            self.stdout.write(self.style.SUCCESS(f"Removed {removed} guild(s); {failed} failed."))
            if failed:
                raise CommandError("Legacy Discord cleanup did not finish; rerun --apply after resolving failures.")
