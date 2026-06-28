from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from api.services.bulk_proactive_outreach import trigger_bulk_proactive_outreach


class Command(BaseCommand):
    help = "Force proactive outreach for an explicit list of persistent agent UUIDs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--agent-id",
            action="append",
            dest="agent_ids",
            default=[],
            help="Persistent agent UUID. May be provided more than once.",
        )
        parser.add_argument(
            "--agent-ids-file",
            help="Path to a newline-, comma-, or whitespace-separated list of persistent agent UUIDs.",
        )
        parser.add_argument(
            "--initiated-by",
            default="management_command.force_proactive_agents",
            help="Audit value stored in proactive trigger metadata.",
        )
        parser.add_argument(
            "--reason",
            default="Manual bulk proactive outreach.",
            help="Reason stored in proactive trigger metadata.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and report what would be queued without creating triggers.",
        )
        parser.add_argument(
            "--allow-recent",
            action="store_true",
            help="Do not skip agents with a proactive trigger in the recent safety window.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Only process the first N parsed identifiers.",
        )

    def handle(self, *args, **options):
        raw_agent_ids = self._raw_agent_ids(options)
        result = trigger_bulk_proactive_outreach(
            raw_agent_ids,
            initiated_by=options["initiated_by"],
            reason=options["reason"],
            dry_run=options["dry_run"],
            skip_recent=not options["allow_recent"],
            limit=options.get("limit"),
        )

        for item in result.items:
            name = f" {item.agent_name}" if item.agent_name else ""
            self.stdout.write(f"{item.status}: {item.agent_id}{name} - {item.message}")

        summary = ", ".join(f"{status}={count}" for status, count in sorted(result.counts.items())) or "none"
        style = self.style.WARNING if options["dry_run"] else self.style.SUCCESS
        self.stdout.write(style(f"Bulk proactive outreach summary: {summary}"))

    def _raw_agent_ids(self, options) -> str:
        parts = list(options.get("agent_ids") or [])
        agent_ids_file = options.get("agent_ids_file")

        if agent_ids_file:
            try:
                parts.append(Path(agent_ids_file).read_text(encoding="utf-8"))
            except OSError as exc:
                raise CommandError(f"Could not read --agent-ids-file {agent_ids_file}: {exc}") from exc

        raw_agent_ids = "\n".join(parts).strip()
        if not raw_agent_ids:
            raise CommandError("Provide at least one agent UUID via --agent-id or --agent-ids-file.")

        return raw_agent_ids
