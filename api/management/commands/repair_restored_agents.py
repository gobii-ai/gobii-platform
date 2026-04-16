import uuid

from django.core.management.base import BaseCommand, CommandError

from api.models import PersistentAgent
from api.services.persistent_agent_restore import PersistentAgentRestoreRepairService


class Command(BaseCommand):
    help = "Repair restored agents whose soft delete released owned comms endpoints or peer links."

    def add_arguments(self, parser):
        parser.add_argument(
            "--agent-id",
            dest="agent_id",
            help="Limit the repair run to a single restored agent UUID.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply repairs. Without this flag the command only reports what it would change.",
        )

    def handle(self, *args, **options):
        agent_id = (options.get("agent_id") or "").strip()
        apply = bool(options.get("apply"))

        queryset = PersistentAgent.objects.alive().order_by("created_at")
        if agent_id:
            try:
                agent_uuid = uuid.UUID(agent_id)
            except ValueError as exc:
                raise CommandError(f"Invalid --agent-id value: {agent_id}") from exc
            queryset = queryset.filter(pk=agent_uuid)

        agents = list(queryset)
        if not agents:
            if agent_id:
                raise CommandError(f"No active agent found for {agent_id}")
            self.stdout.write("No active agents to inspect.")
            return

        action_label = "Repaired" if apply else "Would repair"
        inspected_count = 0
        changed_count = 0

        for agent in agents:
            inspected_count += 1
            result = PersistentAgentRestoreRepairService.repair(
                agent,
                apply=apply,
                provision_email_fallback=False,
            )
            if not (result.has_actions or result.skipped_peer_links or result.notes):
                continue

            if result.has_actions:
                changed_count += 1

            self.stdout.write(
                f"{action_label} agent {agent.id} ({agent.name})"
            )
            if result.restored_endpoint_ids:
                self.stdout.write(f"  endpoints: {', '.join(result.restored_endpoint_ids)}")
            if result.restored_peer_links:
                self.stdout.write(f"  peer_links: {', '.join(result.restored_peer_links)}")
            if result.reattached_conversation_ids:
                self.stdout.write(
                    f"  conversations: {', '.join(result.reattached_conversation_ids)}"
                )
            for skipped in result.skipped_peer_links:
                self.stdout.write(self.style.WARNING(f"  skipped: {skipped}"))
            for note in result.notes:
                self.stdout.write(f"  note: {note}")

        if changed_count:
            summary = f"{action_label} {changed_count} agent(s) after inspecting {inspected_count}."
            self.stdout.write(self.style.SUCCESS(summary))
            return

        self.stdout.write(f"No repairs needed after inspecting {inspected_count} agent(s).")
