import logging

from celery import shared_task
from django.db.models import F
from django.utils import timezone

from api.models import UserFingerprintVisit, UserFingerprintVisitFetchStatusChoices
from api.services.user_fingerprint import (
    FingerprintConfigurationError,
    FingerprintRetryableError,
    FingerprintTerminalError,
    refresh_user_fingerprint_visit,
)


logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    ignore_result=True,
    max_retries=4,
    name="api.fetch_user_fingerprint_visit",
    acks_late=True,
    reject_on_worker_lost=True,
)
def fetch_user_fingerprint_visit_task(self, visit_id: int) -> None:
    visit = UserFingerprintVisit.objects.filter(pk=visit_id).first()
    if visit is None:
        logger.info("Fingerprint visit refresh skipped; visit %s not found.", visit_id)
        return

    if not visit.fingerprint_event_id:
        UserFingerprintVisit.objects.filter(pk=visit.pk).update(
            fetch_status=UserFingerprintVisitFetchStatusChoices.FAILED,
            error_message="Fingerprint event id is missing.",
        )
        return

    if visit.fetch_status == UserFingerprintVisitFetchStatusChoices.SUCCEEDED and visit.raw_payload:
        return

    UserFingerprintVisit.objects.filter(pk=visit.pk).update(
        fetch_status=UserFingerprintVisitFetchStatusChoices.PROCESSING,
        fetch_attempt_count=F("fetch_attempt_count") + 1,
        last_fetch_attempt_at=timezone.now(),
        error_message="",
    )
    visit.refresh_from_db()

    try:
        refresh_user_fingerprint_visit(visit)
    except FingerprintConfigurationError as exc:
        UserFingerprintVisit.objects.filter(pk=visit.pk).update(
            fetch_status=UserFingerprintVisitFetchStatusChoices.NOT_CONFIGURED,
            error_message=str(exc),
        )
        logger.info("Fingerprint visit %s left unfetched: %s", visit.pk, exc)
    except FingerprintRetryableError as exc:
        if self.request.retries >= self.max_retries:
            UserFingerprintVisit.objects.filter(pk=visit.pk).update(
                fetch_status=UserFingerprintVisitFetchStatusChoices.FAILED,
                error_message=str(exc),
            )
            logger.warning("Fingerprint visit %s failed after retries: %s", visit.pk, exc)
            return

        UserFingerprintVisit.objects.filter(pk=visit.pk).update(
            fetch_status=UserFingerprintVisitFetchStatusChoices.PENDING,
            error_message=str(exc),
        )
        raise self.retry(exc=exc, countdown=min(300, 15 * (2 ** self.request.retries)))
    except FingerprintTerminalError as exc:
        UserFingerprintVisit.objects.filter(pk=visit.pk).update(
            fetch_status=UserFingerprintVisitFetchStatusChoices.FAILED,
            error_message=str(exc),
        )
        logger.warning("Fingerprint visit %s failed permanently: %s", visit.pk, exc)
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            UserFingerprintVisit.objects.filter(pk=visit.pk).update(
                fetch_status=UserFingerprintVisitFetchStatusChoices.FAILED,
                error_message=str(exc),
            )
            logger.warning(
                "Fingerprint visit %s failed after unexpected retries: %s",
                visit.pk,
                exc,
                exc_info=True,
            )
            return

        # Keep unexpected task-boundary failures visible while making the row
        # recoverable. This prevents visits from staying wedged in processing.
        UserFingerprintVisit.objects.filter(pk=visit.pk).update(
            fetch_status=UserFingerprintVisitFetchStatusChoices.PENDING,
            error_message=str(exc),
        )
        logger.exception("Fingerprint visit %s hit an unexpected retryable error", visit.pk)
        raise self.retry(exc=exc, countdown=min(300, 15 * (2 ** self.request.retries)))
