import logging
from datetime import timedelta
from dataclasses import dataclass
from django.conf import settings

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import CharField, DateTimeField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from api.models import CommsChannel, PersistentAgentCommsEndpoint, SmsNumber, SmsProvider
from api.models import PersistentAgentMessage
from constants.plans import PlanNames
from util.integrations import twilio_status

try:
    from twilio.base.exceptions import TwilioRestException
    from twilio.rest import Client
except ImportError:  # pragma: no cover - optional dependency in some environments
    class TwilioRestException(Exception):
        pass

    Client = None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmsNumberReleaseResult:
    phone_number: str
    detached_endpoint_count: int
    retired_locally: bool
    twilio_released: bool
    twilio_message: str = ""
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class SmsNumberReleaseCandidate:
    DETACHED_UNUSED = "detached_unused"
    FREE_DORMANT_UNUSED = "free_dormant_unused"

    sms_number_id: str
    phone_number: str
    friendly_name: str
    tier: str
    last_activity_at: object = None
    endpoint_id: str | None = None
    owner_agent_id: str | None = None
    owner_agent_name: str = ""
    owner_email: str = ""
    owner_plan: str = PlanNames.FREE

    @property
    def tier_label(self) -> str:
        if self.tier == self.DETACHED_UNUSED:
            return "Detached unused"
        if self.tier == self.FREE_DORMANT_UNUSED:
            return "Free-plan dormant unused"
        return self.tier

def sms_number_is_in_use(sms_number: SmsNumber) -> bool:
    return PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.SMS,
        address__iexact=sms_number.phone_number,
        owner_agent__isnull=False,
    ).exists()


def retire_sms_number(sms_number: SmsNumber) -> bool:
    """
    Retire a number locally so it remains in history but is never allocated again.
    """
    if sms_number_is_in_use(sms_number):
        raise ValidationError(
            {"phone_number": "Cannot retire an SMS number while it is still assigned to an SMS endpoint."}
        )

    update_fields = []
    if sms_number.is_active:
        sms_number.is_active = False
        update_fields.append("is_active")
    if sms_number.released_at is None:
        sms_number.released_at = timezone.now()
        update_fields.append("released_at")

    if update_fields:
        sms_number.save(update_fields=update_fields)
        return True

    return False


def _get_twilio_release_client() -> Client:
    status = twilio_status()
    if not status.enabled:
        raise ValidationError(
            {"phone_number": status.reason or "Twilio integration is disabled, so numbers cannot be released."}
        )
    if Client is None:
        raise ValidationError({"phone_number": "Twilio SDK is unavailable, so numbers cannot be released."})
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        raise ValidationError({"phone_number": "Twilio credentials are missing, so numbers cannot be released."})
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def _remove_twilio_messaging_service_binding(client: Client, sms_number: SmsNumber) -> None:
    service_sid = sms_number.messaging_service_sid or settings.TWILIO_MESSAGING_SERVICE_SID
    if not service_sid:
        return

    try:
        client.messaging.v1.services(service_sid).phone_numbers(sms_number.sid).delete()
    except TwilioRestException as exc:
        if exc.status == 404:
            logger.info(
                "SMS number %s was already absent from Twilio messaging service %s",
                sms_number.phone_number,
                service_sid,
            )
            return
        raise


def _release_twilio_incoming_number(client: Client, sms_number: SmsNumber) -> str:
    try:
        released = client.incoming_phone_numbers(sms_number.sid).delete()
    except TwilioRestException as exc:
        if exc.status == 404:
            logger.info("SMS number %s was already absent in Twilio", sms_number.phone_number)
            return "already absent in Twilio"
        raise

    if not released:
        raise ValidationError(
            {"phone_number": f"Twilio did not confirm release of {sms_number.phone_number}."}
        )

    return "released in Twilio"


def release_sms_number(sms_number: SmsNumber) -> SmsNumberReleaseResult:
    """
    Fully release a Twilio inventory number while preserving endpoint and message history.
    """
    if sms_number.provider != SmsProvider.TWILIO:
        raise ValidationError({"phone_number": f"{sms_number.phone_number} is not a Twilio inventory number."})

    client = _get_twilio_release_client()

    with transaction.atomic():
        locked_sms_number = SmsNumber.objects.select_for_update().get(pk=sms_number.pk)
        detached_endpoint_count = PersistentAgentCommsEndpoint.objects.filter(
            channel=CommsChannel.SMS,
            address__iexact=locked_sms_number.phone_number,
            owner_agent__isnull=False,
        ).update(owner_agent=None)
        retired_locally = retire_sms_number(locked_sms_number)

    try:
        _remove_twilio_messaging_service_binding(client, locked_sms_number)
        twilio_message = _release_twilio_incoming_number(client, locked_sms_number)
    except TwilioRestException as exc:
        logger.warning(
            "Released SMS number %s locally but failed to release it in Twilio: %s",
            locked_sms_number.phone_number,
            exc,
        )
        return SmsNumberReleaseResult(
            phone_number=locked_sms_number.phone_number,
            detached_endpoint_count=detached_endpoint_count,
            retired_locally=retired_locally,
            twilio_released=False,
            error=str(exc),
        )
    except ValidationError as exc:
        logger.warning(
            "Released SMS number %s locally but Twilio did not confirm release: %s",
            locked_sms_number.phone_number,
            exc,
        )
        return SmsNumberReleaseResult(
            phone_number=locked_sms_number.phone_number,
            detached_endpoint_count=detached_endpoint_count,
            retired_locally=retired_locally,
            twilio_released=False,
            error="; ".join(exc.messages),
        )

    return SmsNumberReleaseResult(
        phone_number=locked_sms_number.phone_number,
        detached_endpoint_count=detached_endpoint_count,
        retired_locally=retired_locally,
        twilio_released=True,
        twilio_message=twilio_message,
    )

def find_sms_number_release_candidates(
    *,
    unused_days: int,
    include_detached_unused: bool = True,
    include_free_dormant_unused: bool = True,
) -> list[SmsNumberReleaseCandidate]:
    """
    Return Twilio SMS numbers that look safe to review for manual release.
    """
    if unused_days < 1:
        raise ValidationError({"unused_days": "Unused days must be at least 1."})
    if not include_detached_unused and not include_free_dormant_unused:
        return []

    endpoint_qs = PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.SMS,
        address__iexact=OuterRef("phone_number"),
    ).order_by()
    latest_message_qs = PersistentAgentMessage.objects.filter(
        Q(
            from_endpoint__channel=CommsChannel.SMS,
            from_endpoint__address__iexact=OuterRef("phone_number"),
        )
        | Q(
            to_endpoint__channel=CommsChannel.SMS,
            to_endpoint__address__iexact=OuterRef("phone_number"),
        )
    ).order_by("-timestamp")

    cutoff = timezone.now() - timedelta(days=unused_days)
    candidate_rows = (
        SmsNumber.objects.filter(
            provider=SmsProvider.TWILIO,
            is_active=True,
            released_at__isnull=True,
        )
        .annotate(
            endpoint_id=Subquery(endpoint_qs.values("id")[:1]),
            endpoint_owner_agent_id=Subquery(endpoint_qs.values("owner_agent_id")[:1]),
            endpoint_owner_agent_name=Coalesce(
                Subquery(endpoint_qs.values("owner_agent__name")[:1]),
                Value("", output_field=CharField()),
            ),
            endpoint_owner_email=Coalesce(
                Subquery(endpoint_qs.values("owner_agent__user__email")[:1]),
                Value("", output_field=CharField()),
            ),
            endpoint_owner_plan=Coalesce(
                Subquery(endpoint_qs.values("owner_agent__user__billing__subscription")[:1]),
                Value(PlanNames.FREE, output_field=CharField()),
            ),
            last_activity_at=Subquery(
                latest_message_qs.values("timestamp")[:1],
                output_field=DateTimeField(),
            ),
        )
        .filter(Q(last_activity_at__lt=cutoff) | Q(last_activity_at__isnull=True))
        .order_by("last_activity_at", "phone_number")
    )

    candidates = []
    for sms_number in candidate_rows:
        if include_detached_unused and sms_number.endpoint_owner_agent_id is None:
            tier = SmsNumberReleaseCandidate.DETACHED_UNUSED
        elif (
            include_free_dormant_unused
            and sms_number.endpoint_owner_agent_id is not None
            and sms_number.endpoint_owner_plan == PlanNames.FREE
        ):
            tier = SmsNumberReleaseCandidate.FREE_DORMANT_UNUSED
        else:
            continue

        candidates.append(
            SmsNumberReleaseCandidate(
                sms_number_id=str(sms_number.id),
                phone_number=sms_number.phone_number,
                friendly_name=sms_number.friendly_name,
                tier=tier,
                last_activity_at=sms_number.last_activity_at,
                endpoint_id=str(sms_number.endpoint_id) if sms_number.endpoint_id else None,
                owner_agent_id=(
                    str(sms_number.endpoint_owner_agent_id)
                    if sms_number.endpoint_owner_agent_id
                    else None
                ),
                owner_agent_name=sms_number.endpoint_owner_agent_name,
                owner_email=sms_number.endpoint_owner_email,
                owner_plan=sms_number.endpoint_owner_plan,
            )
        )

    return candidates
