import uuid

from django.core.management.base import BaseCommand, CommandError

from api.agent.tools.charter_text import repair_structural_literal_newlines
from api.agent.tools.charter_updater import execute_update_charter
from api.models import PersistentAgent


class Command(BaseCommand):
    help = "Repair clearly structural literal \\n sequences in persistent-agent charters."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist repairs. The default is a dry run.",
        )
        parser.add_argument(
            "--agent-id",
            type=uuid.UUID,
            help="Limit the audit or repair to one persistent-agent UUID.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        agent_id = options["agent_id"]
        agents = PersistentAgent.objects.filter(is_deleted=False, charter__contains="\\n")
        if agent_id is not None:
            agents = agents.filter(id=agent_id)

        inspected = repairable = repaired = ambiguous = 0
        failures: list[str] = []
        for agent in agents.order_by("id").iterator():
            inspected += 1
            repaired_charter, structural_count, ambiguous_count = repair_structural_literal_newlines(
                agent.charter
            )
            ambiguous += int(ambiguous_count > 0)
            if structural_count == 0:
                self.stdout.write(
                    f"AMBIGUOUS agent={agent.id} remaining_literal_newlines={ambiguous_count}"
                )
                continue

            repairable += 1
            action = "WOULD_REPAIR"
            if apply_changes:
                result = execute_update_charter(agent, {"new_charter": repaired_charter})
                if not isinstance(result, dict) or result.get("status") != "ok":
                    message = (
                        result.get("message", "Charter update failed.")
                        if isinstance(result, dict)
                        else "Charter update failed."
                    )
                    failures.append(f"agent={agent.id}: {message}")
                    self.stderr.write(self.style.ERROR(f"FAILED {failures[-1]}"))
                    continue
                repaired += 1
                action = "REPAIRED"

            self.stdout.write(
                f"{action} agent={agent.id} structural_newlines={structural_count} "
                f"remaining_literal_newlines={ambiguous_count}"
            )

        mode = "APPLY" if apply_changes else "DRY_RUN"
        self.stdout.write(
            f"{mode} inspected={inspected} repairable={repairable} repaired={repaired} "
            f"ambiguous={ambiguous} failures={len(failures)}"
        )
        if failures:
            raise CommandError(f"Failed to repair {len(failures)} charter(s).")
