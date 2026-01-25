"""
Referral service for handling user referral tracking and credit grants.

This service handles two types of referrals:
1. Direct referrals: User shares their referral code (?ref=CODE)
2. Template referrals: User signs up after viewing a shared agent template

"Last one wins" policy: If a user clicks a direct referral link, then later
hires a template, the template creator gets credit (and vice versa).

Deferred granting (fraud prevention):
When REFERRAL_DEFERRED_GRANT=True (default), credits are not granted at signup.
Instead, they are granted after the referred user completes their first task.
This ensures the referrer only gets rewarded for bringing real, active users.
"""
import logging
from typing import Optional, Tuple

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from api.models import UserReferral, UserAttribution, PersistentAgentTemplate
from constants.grant_types import GrantTypeChoices
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

logger = logging.getLogger(__name__)
User = get_user_model()


class ReferralType:
    """Constants for referral types."""
    DIRECT = "direct"
    TEMPLATE = "template"


class ReferralService:
    """
    Service for processing referral signups and granting credits.

    Usage:
        # In signup signal handler:
        ReferralService.process_signup_referral(
            new_user=user,
            referrer_code=referrer_code,
            template_code=template_code,
        )

        # After first task completion (if deferred granting enabled):
        ReferralService.check_and_grant_deferred_referral_credits(user)
    """

    @classmethod
    def is_deferred_granting_enabled(cls) -> bool:
        """Check if deferred granting is enabled via settings."""
        return getattr(settings, 'REFERRAL_DEFERRED_GRANT', True)

    @classmethod
    def process_signup_referral(
        cls,
        new_user: User,
        referrer_code: Optional[str] = None,
        template_code: Optional[str] = None,
    ) -> Optional[Tuple[str, User]]:
        """
        Process a referral for a newly signed up user.

        If REFERRAL_DEFERRED_GRANT is True (default), this only identifies and
        validates the referral. Credits are granted later via
        check_and_grant_deferred_referral_credits() after first task completion.

        If REFERRAL_DEFERRED_GRANT is False, credits are granted immediately.

        Args:
            new_user: The user who just signed up
            referrer_code: Direct referral code (from ?ref= param)
            template_code: Template code (from hiring a shared template)

        Returns:
            Tuple of (referral_type, referring_user) if a valid referral was found,
            None otherwise.
        """
        if not referrer_code and not template_code:
            return None

        # Determine referral type and find the referring user
        # Template takes precedence if both are present (shouldn't happen with "last one wins")
        result = None
        if template_code:
            result = cls._process_template_referral(new_user, template_code)

        if not result and referrer_code:
            result = cls._process_direct_referral(new_user, referrer_code)

        if not result:
            # Track invalid referral attempt
            Analytics.track_event(
                user_id=new_user.id,
                event=AnalyticsEvent.REFERRAL_SIGNUP_INVALID,
                source=AnalyticsSource.WEB,
                properties={
                    'referrer_code': referrer_code or '',
                    'template_code': template_code or '',
                    'reason': 'code_not_found_or_invalid',
                },
            )
            return None

        referral_type, referring_user = result

        # Track successful referral identification
        Analytics.track_event(
            user_id=new_user.id,
            event=AnalyticsEvent.REFERRAL_SIGNUP_IDENTIFIED,
            source=AnalyticsSource.WEB,
            properties={
                'referral_type': referral_type,
                'referrer_user_id': str(referring_user.id),
                'referrer_code': referrer_code or '',
                'template_code': template_code or '',
                'deferred_granting': cls.is_deferred_granting_enabled(),
            },
        )

        # If deferred granting is disabled, grant credits immediately
        if not cls.is_deferred_granting_enabled():
            grant_type = (
                GrantTypeChoices.REFERRAL_SHARED
                if referral_type == ReferralType.TEMPLATE
                else GrantTypeChoices.REFERRAL
            )
            cls._grant_referral_credits(
                referring_user=referring_user,
                new_user=new_user,
                grant_type=grant_type,
                template_code=template_code if referral_type == ReferralType.TEMPLATE else None,
            )
        else:
            # Track that credits are deferred
            Analytics.track_event(
                user_id=new_user.id,
                event=AnalyticsEvent.REFERRAL_CREDITS_DEFERRED,
                source=AnalyticsSource.WEB,
                properties={
                    'referral_type': referral_type,
                    'referrer_user_id': str(referring_user.id),
                },
            )

        return result

    @classmethod
    def check_and_grant_deferred_referral_credits(cls, user: User) -> bool:
        """
        Check if user has pending referral credits and grant them.

        This should be called after the user completes their first task.
        Only grants if:
        - User has referral attribution (referrer_code or signup_template_code)
        - Credits haven't already been granted (referral_credit_granted_at is null)

        Args:
            user: The user who completed a task

        Returns:
            True if credits were granted, False otherwise.
        """
        try:
            attribution = UserAttribution.objects.get(user=user)
        except UserAttribution.DoesNotExist:
            return False

        # Already granted
        if attribution.referral_credit_granted_at is not None:
            return False

        # No referral to process
        if not attribution.referrer_code and not attribution.signup_template_code:
            return False

        # Determine referral type and find referrer
        referring_user = None
        grant_type = None
        template_code = None

        if attribution.signup_template_code:
            # Template referral
            try:
                template = PersistentAgentTemplate.objects.select_related(
                    'created_by'
                ).get(code=attribution.signup_template_code)
                referring_user = template.created_by
                grant_type = GrantTypeChoices.REFERRAL_SHARED
                template_code = attribution.signup_template_code
            except PersistentAgentTemplate.DoesNotExist:
                logger.warning(
                    "Deferred grant: template not found code=%s user=%s",
                    attribution.signup_template_code,
                    user.id,
                )

        if not referring_user and attribution.referrer_code:
            # Direct referral
            referring_user = UserReferral.get_user_by_code(attribution.referrer_code)
            grant_type = GrantTypeChoices.REFERRAL

        if not referring_user:
            logger.warning(
                "Deferred grant: referrer not found user=%s ref_code=%s template_code=%s",
                user.id,
                attribution.referrer_code,
                attribution.signup_template_code,
            )
            # Mark as processed to avoid repeated lookups
            attribution.referral_credit_granted_at = timezone.now()
            attribution.save(update_fields=['referral_credit_granted_at'])
            return False

        # Don't grant if referring self (shouldn't happen, but defensive)
        if referring_user.id == user.id:
            attribution.referral_credit_granted_at = timezone.now()
            attribution.save(update_fields=['referral_credit_granted_at'])
            return False

        # Grant the credits
        cls._grant_referral_credits(
            referring_user=referring_user,
            new_user=user,
            grant_type=grant_type,
            template_code=template_code,
            deferred=True,
        )

        # Mark as granted
        attribution.referral_credit_granted_at = timezone.now()
        attribution.save(update_fields=['referral_credit_granted_at'])

        logger.info(
            "Deferred referral credits granted: new_user=%s referrer=%s type=%s",
            user.id,
            referring_user.id,
            grant_type,
        )

        # Track the deferred grant
        Analytics.track_event(
            user_id=user.id,
            event=AnalyticsEvent.REFERRAL_CREDITS_GRANTED,
            source=AnalyticsSource.WEB,
            properties={
                'referral_type': ReferralType.TEMPLATE if template_code else ReferralType.DIRECT,
                'referrer_user_id': str(referring_user.id),
                'grant_type': grant_type,
                'template_code': template_code or '',
                'deferred': True,
                'trigger': 'first_task_completion',
            },
        )

        return True

    @classmethod
    def _process_direct_referral(
        cls,
        new_user: User,
        referrer_code: str,
    ) -> Optional[Tuple[str, User]]:
        """
        Process a direct user-to-user referral.

        Args:
            new_user: The user who just signed up
            referrer_code: The referral code from the URL

        Returns:
            Tuple of (ReferralType.DIRECT, referring_user) if valid, None otherwise.
        """
        referring_user = UserReferral.get_user_by_code(referrer_code)
        if not referring_user:
            logger.warning(
                "Direct referral code not found: code=%s new_user=%s",
                referrer_code,
                new_user.id,
            )
            return None

        if referring_user.id == new_user.id:
            logger.warning(
                "User attempted to refer themselves: user=%s code=%s",
                new_user.id,
                referrer_code,
            )
            return None

        logger.info(
            "Direct referral identified: new_user=%s referred_by=%s code=%s",
            new_user.id,
            referring_user.id,
            referrer_code,
        )

        return (ReferralType.DIRECT, referring_user)

    @classmethod
    def _process_template_referral(
        cls,
        new_user: User,
        template_code: str,
    ) -> Optional[Tuple[str, User]]:
        """
        Process a referral from a shared agent template.

        Args:
            new_user: The user who just signed up
            template_code: The template code they hired before signup

        Returns:
            Tuple of (ReferralType.TEMPLATE, template_creator) if valid, None otherwise.
        """
        try:
            template = PersistentAgentTemplate.objects.select_related(
                'created_by'
            ).get(code=template_code)
        except PersistentAgentTemplate.DoesNotExist:
            logger.warning(
                "Template referral code not found: code=%s new_user=%s",
                template_code,
                new_user.id,
            )
            return None

        referring_user = template.created_by
        if not referring_user:
            logger.info(
                "Template has no creator (system template): code=%s new_user=%s",
                template_code,
                new_user.id,
            )
            return None

        if referring_user.id == new_user.id:
            logger.info(
                "User signed up via their own template: user=%s template=%s",
                new_user.id,
                template_code,
            )
            return None

        logger.info(
            "Template referral identified: new_user=%s referred_by=%s template=%s",
            new_user.id,
            referring_user.id,
            template_code,
        )

        return (ReferralType.TEMPLATE, referring_user)

    @classmethod
    def _grant_referral_credits(
        cls,
        referring_user: User,
        new_user: User,
        grant_type: str,
        template_code: Optional[str] = None,
        deferred: bool = False,
    ) -> None:
        """
        Grant credits to the referring user.

        TODO: Implement when credit amounts and rules are defined.

        Args:
            referring_user: User who made the referral
            new_user: User who signed up
            grant_type: GrantTypeChoices.REFERRAL or GrantTypeChoices.REFERRAL_SHARED
            template_code: Template code if this was a template referral
            deferred: Whether this is a deferred grant (after first task)
        """
        # TODO: Implement credit granting
        # - Determine credit amount based on plan/config
        # - Create TaskCredit record with appropriate grant_type
        # - Optionally grant welcome credits to new_user
        logger.info(
            "Referral credit grant (TODO): referrer=%s new_user=%s type=%s template=%s deferred=%s",
            referring_user.id,
            new_user.id,
            grant_type,
            template_code or '(none)',
            deferred,
        )

        # Track the grant (for immediate grants; deferred grants tracked separately)
        if not deferred:
            Analytics.track_event(
                user_id=new_user.id,
                event=AnalyticsEvent.REFERRAL_CREDITS_GRANTED,
                source=AnalyticsSource.WEB,
                properties={
                    'referral_type': ReferralType.TEMPLATE if template_code else ReferralType.DIRECT,
                    'referrer_user_id': str(referring_user.id),
                    'grant_type': grant_type,
                    'template_code': template_code or '',
                    'deferred': False,
                    'trigger': 'signup',
                },
            )

    @classmethod
    def get_or_create_referral_code(cls, user: User) -> str:
        """
        Get or create a referral code for a user.

        Args:
            user: The user who wants to share their referral link

        Returns:
            The user's referral code
        """
        referral = UserReferral.get_or_create_for_user(user)
        return referral.referral_code

    @classmethod
    def get_referral_link(cls, user: User, base_url: str = "", track: bool = True) -> str:
        """
        Get the full referral link for a user.

        Args:
            user: The user who wants to share their referral link
            base_url: Base URL (e.g., "https://gobii.ai")
            track: Whether to track this as an analytics event (default True)

        Returns:
            Full referral URL (e.g., "https://gobii.ai/?ref=ABC123")
        """
        code = cls.get_or_create_referral_code(user)
        link = f"{base_url}/?ref={code}"

        if track:
            Analytics.track_event(
                user_id=user.id,
                event=AnalyticsEvent.REFERRAL_LINK_GENERATED,
                source=AnalyticsSource.WEB,
                properties={
                    'referral_code': code,
                },
            )

        return link

    @classmethod
    def has_pending_referral_credit(cls, user: User) -> bool:
        """
        Check if user has a pending referral credit that hasn't been granted yet.

        Args:
            user: The user to check

        Returns:
            True if there's a pending referral credit, False otherwise.
        """
        try:
            attribution = UserAttribution.objects.get(user=user)
        except UserAttribution.DoesNotExist:
            return False

        if attribution.referral_credit_granted_at is not None:
            return False

        return bool(attribution.referrer_code or attribution.signup_template_code)
