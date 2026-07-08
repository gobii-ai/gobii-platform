import logging

from django.core.management.base import BaseCommand, CommandError

from api.models import PersistentAgentTemplate

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Grant anonymous read access to uploaded public template social images in GCS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report images that would be updated without changing object ACLs.",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=200,
            help="Number of templates to inspect per database batch.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        chunk_size = options["chunk_size"]
        field = PersistentAgentTemplate._meta.get_field("social_image")
        storage = field.storage
        wrapped_storage = getattr(storage, "_wrapped_storage", storage)

        if wrapped_storage.__class__.__module__ != "storages.backends.gcloud":
            self.stdout.write(
                self.style.WARNING(
                    "Public template social image storage is not GCS; no ACL repair needed."
                )
            )
            return

        queryset = (
            PersistentAgentTemplate.objects.exclude(social_image__isnull=True)
            .exclude(social_image="")
            .only("id", "social_image")
            .order_by("id")
        )

        found = 0
        updated = 0
        missing = 0

        for template in queryset.iterator(chunk_size=chunk_size):
            found += 1
            storage_name = template.social_image.name
            if dry_run:
                self.stdout.write(f"[DRY RUN] Would make public: {storage_name}")
                continue

            try:
                result = _make_gcs_object_public(wrapped_storage, storage_name)
            except PublicAclUpdateRejected as exc:
                raise CommandError(str(exc)) from exc
            if result == "updated":
                updated += 1
                self.stdout.write(f"Made public: {storage_name}")
            elif result == "missing":
                missing += 1
                self.stdout.write(self.style.WARNING(f"Missing object: {storage_name}"))

        if dry_run:
            summary = f"[DRY RUN] Found {found} uploaded public template social image(s)."
        else:
            summary = (
                f"Public template social image ACL repair complete. "
                f"found={found} updated={updated} missing={missing}"
            )
        self.stdout.write(self.style.SUCCESS(summary))
        logger.info(summary)


def _make_gcs_object_public(storage, name: str) -> str:
    from google.api_core.exceptions import BadRequest, Forbidden
    from google.cloud.exceptions import NotFound
    from storages.utils import clean_name

    normalized_name = storage._normalize_name(clean_name(name))
    blob = storage.bucket.blob(normalized_name)
    try:
        blob.make_public()
    except NotFound:
        return "missing"
    except (BadRequest, Forbidden) as exc:
        raise PublicAclUpdateRejected(
            "GCS rejected the object ACL update. If uniform bucket-level access is enabled, "
            "grant public read via bucket IAM or reconfigure the bucket before running this command."
        ) from exc
    return "updated"


class PublicAclUpdateRejected(RuntimeError):
    pass
