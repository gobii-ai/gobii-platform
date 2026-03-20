import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from api.models import PersistentAgentSecret
from api.services.persistent_agent_secrets import format_validation_error


class Command(BaseCommand):
    help = "Report invalid PersistentAgentSecret rows without modifying data."

    def handle(self, *args, **options):
        invalid_count = 0

        for secret in PersistentAgentSecret.objects.select_related("agent").order_by("agent_id", "created_at", "id"):
            try:
                secret.full_clean()
            except ValidationError as exc:
                invalid_count += 1
                self.stdout.write(
                    json.dumps(
                        {
                            "agent_id": str(secret.agent_id),
                            "secret_id": str(secret.id),
                            "key": secret.key,
                            "domain_pattern": secret.domain_pattern,
                            "created_at": secret.created_at.isoformat() if secret.created_at else None,
                            "error": format_validation_error(exc),
                        },
                        sort_keys=True,
                    )
                )

        if invalid_count:
            self.stdout.write(
                self.style.WARNING(
                    f"Found {invalid_count} invalid persistent agent secret(s)."
                )
            )
            return

        self.stdout.write(self.style.SUCCESS("No invalid persistent agent secrets found."))
