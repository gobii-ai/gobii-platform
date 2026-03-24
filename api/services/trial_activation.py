from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from api.models import TaskCredit, UserTrialActivation
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


TRIAL_ACTIVATION_VERSION = 1
ACTIVATION_REASON_NOT_IMPLEMENTED = "criteria_not_implemented_v1"
ACTIVATION_REASON_NOT_INDIVIDUAL_TRIAL_USER = "not_individual_trial_user"
ACTIVATION_REASON_ALREADY_ACTIVATED = "already_activated"


@dataclass(frozen=True)
class TrialActivationAssessmentResult:
    activated: bool
    newly_activated: bool
    is_individual_trial_user: bool
    activation_version: int
    activation_reason: str


def _is_individual_trial_user(user) -> bool:
    if not user or not getattr(user, "pk", None):
        return False
    return TaskCredit.objects.filter(user=user, free_trial_start=True).exists()


def _evaluate_trial_activation(user) -> tuple[bool, str]:
    # TODO: figure out if activated
    return False, ACTIVATION_REASON_NOT_IMPLEMENTED


def _get_or_create_activation_record(user) -> UserTrialActivation:
    try:
        return UserTrialActivation.objects.select_for_update().get(user=user)
    except UserTrialActivation.DoesNotExist:
        try:
            return UserTrialActivation.objects.create(user=user)
        except IntegrityError:
            return UserTrialActivation.objects.select_for_update().get(user=user)


def assess_trial_user_activation(
    user,
    *,
    source: AnalyticsSource = AnalyticsSource.API,
    trigger: str | None = None,
) -> TrialActivationAssessmentResult:
    if not user or not getattr(user, "pk", None):
        return TrialActivationAssessmentResult(
            activated=False,
            newly_activated=False,
            is_individual_trial_user=False,
            activation_version=TRIAL_ACTIVATION_VERSION,
            activation_reason=ACTIVATION_REASON_NOT_INDIVIDUAL_TRIAL_USER,
        )

    now = timezone.now()
    is_individual_trial_user = _is_individual_trial_user(user)

    with transaction.atomic():
        activation = _get_or_create_activation_record(user)
        newly_activated = False

        if activation.is_activated:
            activated = True
            reason = activation.activation_reason or ACTIVATION_REASON_ALREADY_ACTIVATED
        elif not is_individual_trial_user:
            activated = False
            reason = ACTIVATION_REASON_NOT_INDIVIDUAL_TRIAL_USER
        else:
            activated, reason = _evaluate_trial_activation(user)
            if activated:
                newly_activated = True
                activation.is_activated = True
                activation.activated_at = activation.activated_at or now

        activation.last_assessed_at = now
        activation.activation_version = TRIAL_ACTIVATION_VERSION
        if not activation.is_activated or newly_activated or not activation.activation_reason:
            activation.activation_reason = reason

        activation.save(
            update_fields=[
                "is_activated",
                "activated_at",
                "last_assessed_at",
                "activation_version",
                "activation_reason",
                "updated_at",
            ]
        )

    properties = {
        "activated": activation.is_activated,
        "newly_activated": newly_activated,
        "activation_version": activation.activation_version,
        "activation_reason": activation.activation_reason,
        "is_individual_trial_user": is_individual_trial_user,
    }
    if trigger:
        properties["trigger"] = trigger

    Analytics.track_event(
        user_id=user.id,
        event=AnalyticsEvent.ACTIVATION_ASSESSED,
        source=source,
        properties=properties,
    )

    return TrialActivationAssessmentResult(
        activated=activation.is_activated,
        newly_activated=newly_activated,
        is_individual_trial_user=is_individual_trial_user,
        activation_version=activation.activation_version,
        activation_reason=activation.activation_reason,
    )
