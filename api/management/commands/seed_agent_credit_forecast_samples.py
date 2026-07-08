from django.core.management.base import BaseCommand

from api.services.agent_credit_forecast_samples import DEFAULT_BATCH_SIZE, seed_agent_credit_forecast_samples


class Command(BaseCommand):
    help = "Seed pgvector-backed historical agent credit forecast samples from the configured database."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=5000)
        parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
        parser.add_argument("--generate-embeddings", action="store_true")
        parser.add_argument("--skip-existing-embeddings", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        result = seed_agent_credit_forecast_samples(
            limit=max(1, int(options["limit"])),
            generate_embeddings=bool(options["generate_embeddings"]),
            skip_existing_embeddings=bool(options["skip_existing_embeddings"]),
            dry_run=bool(options["dry_run"]),
            batch_size=max(1, int(options["batch_size"])),
        )

        if result.dry_run:
            self.stdout.write(self.style.SUCCESS(f"Dry run found {result.upserted} historical agent samples."))
            return

        embedding_summary = ""
        if options["generate_embeddings"]:
            embedding_summary = (
                f" and generated {result.embedded} embeddings"
                f" ({result.skipped_embeddings} already current)"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {result.upserted} historical agent forecast samples{embedding_summary}."
            )
        )
