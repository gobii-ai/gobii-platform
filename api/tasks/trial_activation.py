from celery import shared_task
from django.contrib.auth import get_user_model

from api.services.trial_activation import assess_trial_user_activation
from marketing_events.api import capi
from marketing_events.constants import AD_CAPI_PROVIDER_TARGETS
from marketing_events.context import build_marketing_context_from_user
from util.analytics import AnalyticsSource


User = get_user_model()


@shared_task(name="api.tasks.assess_trial_user_activation")
def assess_trial_user_activation_task(user_id: int, trigger: str = "") -> bool:
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return False

    result = assess_trial_user_activation(
        user,
        source=AnalyticsSource.API,
        trigger=trigger or None,
    )
    if not result.newly_activated or not result.is_individual_trial_user:
        return result.activated

    properties = {
        "activation_version": result.activation_version,
        "activation_reason": result.activation_reason,
        "event_id": f"trial-activated:{user.id}:v{result.activation_version}",
    }
    if trigger:
        properties["activation_trigger"] = trigger

    capi(
        user=user,
        event_name="Activated",
        properties=properties,
        request=None,
        context=build_marketing_context_from_user(
            user,
            synthesized_fbc_source="api.tasks.assess_trial_user_activation_task",
        ),
        provider_targets=AD_CAPI_PROVIDER_TARGETS,
    )
    return True
