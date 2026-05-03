import logging

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef

from api.models import UserFlagAssignment, UserFlagChoiceGroup, UserFlagChoiceOption, UserFlagDefinition

logger = logging.getLogger(__name__)


class UnknownUserFlagError(ValueError):
    """Raised when code references a flag slug that is not configured."""


def get_user_flag_definition(flag: UserFlagDefinition | str) -> UserFlagDefinition:
    if isinstance(flag, UserFlagDefinition):
        if not flag.pk:
            raise UnknownUserFlagError("User flag definition must be saved before use.")
        return flag

    slug = str(flag or "").strip()
    if not slug:
        raise UnknownUserFlagError("User flag slug is required.")

    definition = UserFlagDefinition.objects.filter(slug=slug).first()
    if definition is None:
        raise UnknownUserFlagError(f"Unknown user flag slug: {slug}")

    return definition


def get_enabled_user_flag_slugs(user) -> set[str]:
    if not user or not getattr(user, "pk", None):
        return set()

    return set(
        UserFlagAssignment.objects.filter(user=user).values_list("flag__slug", flat=True)
    )


def has_user_flag(flag: UserFlagDefinition | str, user) -> bool:
    if not user or not getattr(user, "pk", None):
        return False

    definition = get_user_flag_definition(flag)
    return UserFlagAssignment.objects.filter(user=user, flag=definition).exists()


def _sync_user_flags_to_analytics(user) -> None:
    try:
        from util.analytics import Analytics

        Analytics.identify(user.id, {})
    except Exception:
        logger.exception(
            "Failed to sync dynamic user flags to analytics for user %s",
            getattr(user, "id", None),
        )


def set_user_flag(flag: UserFlagDefinition | str, user, enabled: bool) -> bool:
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean.")
    if not user or not getattr(user, "pk", None):
        raise ValueError("User must be saved before setting a flag.")

    definition = get_user_flag_definition(flag)

    if enabled:
        created = False
        try:
            _, created = UserFlagAssignment.objects.get_or_create(user=user, flag=definition)
        except IntegrityError:
            if not UserFlagAssignment.objects.filter(user=user, flag=definition).exists():
                raise
        if created:
            _sync_user_flags_to_analytics(user)
        return True

    deleted_count, _ = UserFlagAssignment.objects.filter(user=user, flag=definition).delete()
    if deleted_count:
        _sync_user_flags_to_analytics(user)
    return False


def get_selected_user_flag_choice_option(group: UserFlagChoiceGroup, user) -> UserFlagChoiceOption | None:
    if not user or not getattr(user, "pk", None):
        return None
    if not group or not getattr(group, "pk", None):
        return None

    return (
        UserFlagChoiceOption.objects.filter(
            group=group,
            flag__user_assignments__user=user,
        )
        .select_related("flag", "group")
        .order_by("label", "pk")
        .first()
    )


def _ensure_user_flag_assignment(user, flag_id: int) -> bool:
    try:
        with transaction.atomic():
            _, created = UserFlagAssignment.objects.get_or_create(user=user, flag_id=flag_id)
    except IntegrityError:
        if not UserFlagAssignment.objects.filter(user=user, flag_id=flag_id).exists():
            raise
        return False
    return created


def set_user_flag_choice(
    group: UserFlagChoiceGroup,
    user,
    option: UserFlagChoiceOption | None,
) -> UserFlagChoiceOption | None:
    if not group or not getattr(group, "pk", None):
        raise ValueError("User flag choice group must be saved before setting a choice.")
    if not user or not getattr(user, "pk", None):
        raise ValueError("User must be saved before setting a flag choice.")
    if option is not None:
        if not getattr(option, "pk", None):
            raise ValueError("User flag choice option must be saved before setting a choice.")
        if option.group_id != group.pk:
            raise ValueError("User flag choice option does not belong to the supplied group.")

    option_flag_ids = set(
        UserFlagChoiceOption.objects.filter(group=group).values_list("flag_id", flat=True)
    )
    selected_flag_id = option.flag_id if option is not None else None
    desired_flag_ids = {selected_flag_id} if selected_flag_id else set()

    with transaction.atomic():
        get_user_model().objects.select_for_update().only("pk").get(pk=user.pk)
        existing_flag_ids = set(
            UserFlagAssignment.objects.filter(
                user=user,
                flag_id__in=option_flag_ids,
            ).values_list("flag_id", flat=True)
        )
        if existing_flag_ids == desired_flag_ids:
            return option

        UserFlagAssignment.objects.filter(
            user=user,
            flag_id__in=option_flag_ids - desired_flag_ids,
        ).delete()

        if selected_flag_id and selected_flag_id not in existing_flag_ids:
            _ensure_user_flag_assignment(user, selected_flag_id)

    _sync_user_flags_to_analytics(user)
    return option


def filter_users_by_flag(queryset, flag: UserFlagDefinition | str, *, enabled: bool):
    definition = get_user_flag_definition(flag)
    annotation_name = f"_has_user_flag_{definition.pk}"
    assignment_exists = UserFlagAssignment.objects.filter(
        user_id=OuterRef("pk"),
        flag_id=definition.pk,
    )
    queryset = queryset.annotate(**{annotation_name: Exists(assignment_exists)})
    return queryset.filter(**{annotation_name: enabled})
