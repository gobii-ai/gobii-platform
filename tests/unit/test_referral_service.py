"""Tests for the referral service."""
from django.test import TestCase, RequestFactory, tag, override_settings
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock

from api.models import UserReferral, UserAttribution, PersistentAgentTemplate, PublicProfile, BrowserUseAgentTask
from api.services.referral_service import ReferralService, ReferralType
from middleware.utm_capture import UTMTrackingMiddleware

User = get_user_model()


@tag('referral_batch')
class UserReferralModelTests(TestCase):
    """Tests for UserReferral model."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123',
        )

    def test_generate_code_creates_unique_code(self):
        """Test that generate_code creates a unique alphanumeric code."""
        code = UserReferral.generate_code()
        self.assertEqual(len(code), 8)
        # Should not contain ambiguous characters
        for char in '0OI1L':
            self.assertNotIn(char, code)

    def test_get_or_create_for_user_creates_new(self):
        """Test creating a new referral code for a user."""
        referral = UserReferral.get_or_create_for_user(self.user)
        self.assertIsNotNone(referral)
        self.assertEqual(referral.user, self.user)
        self.assertEqual(len(referral.referral_code), 8)

    def test_get_or_create_for_user_returns_existing(self):
        """Test that get_or_create returns existing code."""
        referral1 = UserReferral.get_or_create_for_user(self.user)
        referral2 = UserReferral.get_or_create_for_user(self.user)
        self.assertEqual(referral1.id, referral2.id)
        self.assertEqual(referral1.referral_code, referral2.referral_code)

    def test_get_user_by_code_found(self):
        """Test looking up user by referral code."""
        referral = UserReferral.get_or_create_for_user(self.user)
        found_user = UserReferral.get_user_by_code(referral.referral_code)
        self.assertEqual(found_user, self.user)

    def test_get_user_by_code_not_found(self):
        """Test that invalid code returns None."""
        found_user = UserReferral.get_user_by_code('INVALID1')
        self.assertIsNone(found_user)


@tag('referral_batch')
class ReferralServiceTests(TestCase):
    """Tests for ReferralService."""

    def setUp(self):
        self.referrer = User.objects.create_user(
            username='referrer',
            email='referrer@example.com',
            password='testpass123',
        )
        self.new_user = User.objects.create_user(
            username='newuser',
            email='newuser@example.com',
            password='testpass123',
        )
        self.referrer_referral = UserReferral.get_or_create_for_user(self.referrer)

    def test_process_direct_referral_valid(self):
        """Test processing a valid direct referral."""
        result = ReferralService.process_signup_referral(
            new_user=self.new_user,
            referrer_code=self.referrer_referral.referral_code,
        )
        self.assertIsNotNone(result)
        referral_type, referring_user = result
        self.assertEqual(referral_type, ReferralType.DIRECT)
        self.assertEqual(referring_user, self.referrer)

    def test_process_direct_referral_invalid_code(self):
        """Test that invalid referral code returns None."""
        result = ReferralService.process_signup_referral(
            new_user=self.new_user,
            referrer_code='INVALID1',
        )
        self.assertIsNone(result)

    def test_process_direct_referral_self_referral(self):
        """Test that self-referral is rejected."""
        result = ReferralService.process_signup_referral(
            new_user=self.referrer,
            referrer_code=self.referrer_referral.referral_code,
        )
        self.assertIsNone(result)

    def test_process_no_referral(self):
        """Test with no referral codes."""
        result = ReferralService.process_signup_referral(
            new_user=self.new_user,
        )
        self.assertIsNone(result)

    def test_get_referral_link(self):
        """Test generating referral link."""
        link = ReferralService.get_referral_link(
            self.referrer,
            base_url='https://gobii.ai',
        )
        self.assertIn('?ref=', link)
        self.assertIn(self.referrer_referral.referral_code, link)

    @override_settings(REFERRAL_DEFERRED_GRANT=True)
    def test_deferred_granting_enabled_by_default(self):
        """Test that deferred granting is enabled by default."""
        self.assertTrue(ReferralService.is_deferred_granting_enabled())

    @override_settings(REFERRAL_DEFERRED_GRANT=False)
    def test_deferred_granting_can_be_disabled(self):
        """Test that deferred granting can be disabled via settings."""
        self.assertFalse(ReferralService.is_deferred_granting_enabled())


@tag('referral_batch')
class DeferredReferralGrantTests(TestCase):
    """Tests for deferred referral credit granting."""

    def setUp(self):
        self.referrer = User.objects.create_user(
            username='referrer',
            email='referrer@example.com',
            password='testpass123',
        )
        self.new_user = User.objects.create_user(
            username='newuser',
            email='newuser@example.com',
            password='testpass123',
        )
        self.referrer_referral = UserReferral.get_or_create_for_user(self.referrer)

    def _create_completed_task(self, user):
        """Helper to create a completed browser task for a user."""
        return BrowserUseAgentTask.objects.create(
            user=user,
            status=BrowserUseAgentTask.StatusChoices.COMPLETED,
            task='Test task',
        )

    def test_has_pending_referral_credit_true(self):
        """Test has_pending_referral_credit returns True when pending."""
        UserAttribution.objects.create(
            user=self.new_user,
            referrer_code=self.referrer_referral.referral_code,
        )
        self.assertTrue(ReferralService.has_pending_referral_credit(self.new_user))

    def test_has_pending_referral_credit_false_when_granted(self):
        """Test has_pending_referral_credit returns False when already granted."""
        from django.utils import timezone
        UserAttribution.objects.create(
            user=self.new_user,
            referrer_code=self.referrer_referral.referral_code,
            referral_credit_granted_at=timezone.now(),
        )
        self.assertFalse(ReferralService.has_pending_referral_credit(self.new_user))

    def test_has_pending_referral_credit_false_no_referral(self):
        """Test has_pending_referral_credit returns False when no referral."""
        UserAttribution.objects.create(user=self.new_user)
        self.assertFalse(ReferralService.has_pending_referral_credit(self.new_user))

    def test_has_pending_referral_credit_with_task_requirement(self):
        """Test has_pending_referral_credit with require_completed_task=True."""
        UserAttribution.objects.create(
            user=self.new_user,
            referrer_code=self.referrer_referral.referral_code,
        )
        # Without completed task
        self.assertFalse(ReferralService.has_pending_referral_credit(
            self.new_user, require_completed_task=True
        ))

        # With completed task
        self._create_completed_task(self.new_user)
        self.assertTrue(ReferralService.has_pending_referral_credit(
            self.new_user, require_completed_task=True
        ))

    def test_check_and_grant_deferred_credits_success(self):
        """Test granting deferred credits after first task."""
        self._create_completed_task(self.new_user)
        UserAttribution.objects.create(
            user=self.new_user,
            referrer_code=self.referrer_referral.referral_code,
        )
        result = ReferralService.check_and_grant_deferred_referral_credits(self.new_user)
        self.assertTrue(result)

        # Verify it was marked as granted
        attribution = UserAttribution.objects.get(user=self.new_user)
        self.assertIsNotNone(attribution.referral_credit_granted_at)

    def test_check_and_grant_deferred_credits_no_completed_task(self):
        """Test that credits aren't granted without a completed task."""
        UserAttribution.objects.create(
            user=self.new_user,
            referrer_code=self.referrer_referral.referral_code,
        )
        result = ReferralService.check_and_grant_deferred_referral_credits(self.new_user)
        self.assertFalse(result)

        # Verify it was NOT marked as granted
        attribution = UserAttribution.objects.get(user=self.new_user)
        self.assertIsNone(attribution.referral_credit_granted_at)

    def test_check_and_grant_deferred_credits_already_granted(self):
        """Test that credits aren't granted twice."""
        from django.utils import timezone
        self._create_completed_task(self.new_user)
        UserAttribution.objects.create(
            user=self.new_user,
            referrer_code=self.referrer_referral.referral_code,
            referral_credit_granted_at=timezone.now(),
        )
        result = ReferralService.check_and_grant_deferred_referral_credits(self.new_user)
        self.assertFalse(result)

    def test_check_and_grant_deferred_credits_idempotent(self):
        """Test that repeated calls don't grant credits twice."""
        self._create_completed_task(self.new_user)
        UserAttribution.objects.create(
            user=self.new_user,
            referrer_code=self.referrer_referral.referral_code,
        )
        # First call should succeed
        result1 = ReferralService.check_and_grant_deferred_referral_credits(self.new_user)
        self.assertTrue(result1)

        # Second call should fail (already granted)
        result2 = ReferralService.check_and_grant_deferred_referral_credits(self.new_user)
        self.assertFalse(result2)

    def test_check_and_grant_deferred_credits_no_attribution(self):
        """Test graceful handling when user has no attribution record."""
        result = ReferralService.check_and_grant_deferred_referral_credits(self.new_user)
        self.assertFalse(result)

    def test_check_and_grant_deferred_credits_invalid_code(self):
        """Test handling when referral code is no longer valid."""
        self._create_completed_task(self.new_user)
        UserAttribution.objects.create(
            user=self.new_user,
            referrer_code='INVALID_CODE',
        )
        result = ReferralService.check_and_grant_deferred_referral_credits(self.new_user)
        self.assertFalse(result)

        # Should still be marked as processed to avoid repeated lookups
        attribution = UserAttribution.objects.get(user=self.new_user)
        self.assertIsNotNone(attribution.referral_credit_granted_at)


class MockSession(dict):
    """Mock session that supports the modified attribute."""
    modified = False


@tag('referral_batch')
class UTMMiddlewareReferralTests(TestCase):
    """Tests for referral capture in UTM middleware."""

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = UTMTrackingMiddleware(lambda r: r)

    def _make_session(self, data=None):
        session = MockSession(data or {})
        session.modified = False
        return session

    def test_captures_ref_param(self):
        """Test that ?ref= param is captured in session."""
        request = self.factory.get('/?ref=ABC123')
        request.session = self._make_session()
        self.middleware(request)
        self.assertEqual(request.session.get('referrer_code'), 'ABC123')

    def test_ref_clears_template_code(self):
        """Test that ref param clears any existing template code."""
        request = self.factory.get('/?ref=ABC123')
        request.session = self._make_session({'signup_template_code': 'OLD_TEMPLATE'})
        self.middleware(request)
        self.assertEqual(request.session.get('referrer_code'), 'ABC123')
        self.assertNotIn('signup_template_code', request.session)

    def test_ref_updates_on_new_code(self):
        """Test that ref param is updated when a new code is provided."""
        request = self.factory.get('/?ref=NEW123')
        request.session = self._make_session({'referrer_code': 'OLD123'})
        self.middleware(request)
        self.assertEqual(request.session.get('referrer_code'), 'NEW123')

    def test_no_ref_param_preserves_existing(self):
        """Test that without ref param, existing code is preserved."""
        request = self.factory.get('/?utm_source=google')
        request.session = self._make_session({'referrer_code': 'EXISTING'})
        self.middleware(request)
        self.assertEqual(request.session.get('referrer_code'), 'EXISTING')
