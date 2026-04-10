from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase, override_settings, tag
from django.urls import reverse

from api.admin import CustomUserAdmin, CustomUserAdminForm
from api.models import (
    ImmutableUserFlagSlugError,
    UserFlagAssignment,
    UserFlagDefinition,
)
from api.services.user_flags import (
    UnknownUserFlagError,
    filter_users_by_flag,
    has_user_flag,
    set_user_flag,
)
from util.analytics import Analytics


User = get_user_model()


@tag("batch_user_flags")
class DynamicUserFlagServiceTests(TestCase):
    def setUp(self):
        self.flag = UserFlagDefinition.objects.create(
            slug="example_flag",
            description="Example dynamic user flag.",
        )
        self.user = User.objects.create_user(
            username="flagged-service-user@example.com",
            email="flagged-service-user@example.com",
            password="pw",
        )

    def test_has_user_flag_defaults_false_without_assignment(self):
        self.assertFalse(has_user_flag(self.flag.slug, self.user))
        self.assertFalse(UserFlagAssignment.objects.filter(user=self.user, flag=self.flag).exists())

    def test_set_user_flag_can_enable_and_disable_assignment(self):
        set_user_flag(self.flag.slug, self.user, True)
        self.assertTrue(has_user_flag(self.flag.slug, self.user))
        self.assertTrue(UserFlagAssignment.objects.filter(user=self.user, flag=self.flag).exists())

        set_user_flag(self.flag.slug, self.user, False)
        self.assertFalse(has_user_flag(self.flag.slug, self.user))
        self.assertFalse(UserFlagAssignment.objects.filter(user=self.user, flag=self.flag).exists())

    def test_unknown_user_flag_raises_explicit_exception(self):
        with self.assertRaises(UnknownUserFlagError):
            has_user_flag("missing_flag", self.user)

        with self.assertRaises(UnknownUserFlagError):
            set_user_flag("missing_flag", self.user, True)

    def test_filter_users_by_flag_supports_true_and_false(self):
        enabled_user = self.user
        disabled_user = User.objects.create_user(
            username="disabled-service-user@example.com",
            email="disabled-service-user@example.com",
            password="pw",
        )
        set_user_flag(self.flag, enabled_user, True)

        enabled_ids = list(
            filter_users_by_flag(User.objects.order_by("id"), self.flag, enabled=True).values_list("id", flat=True)
        )
        disabled_ids = list(
            filter_users_by_flag(User.objects.order_by("id"), self.flag, enabled=False).values_list("id", flat=True)
        )

        self.assertEqual(enabled_ids, [enabled_user.id])
        self.assertIn(disabled_user.id, disabled_ids)
        self.assertNotIn(enabled_user.id, disabled_ids)

    def test_flag_slug_is_immutable(self):
        self.flag.slug = "renamed_flag"

        with self.assertRaises(ImmutableUserFlagSlugError):
            self.flag.save()

    @override_settings(SEGMENT_WRITE_KEY="test-segment-key")
    @patch("util.analytics.analytics.identify")
    def test_analytics_identify_includes_dynamic_user_flags_trait(self, mock_segment_identify):
        beta_flag = UserFlagDefinition.objects.create(
            slug="beta_flag",
            description="Second dynamic flag for analytics coverage.",
        )
        UserFlagAssignment.objects.create(user=self.user, flag=beta_flag)
        UserFlagAssignment.objects.create(user=self.user, flag=self.flag)

        Analytics.identify(self.user.id, {"plan": "free"})

        mock_segment_identify.assert_called_once()
        identify_user_id, identify_traits, identify_context = mock_segment_identify.call_args.args
        self.assertEqual(identify_user_id, self.user.id)
        self.assertEqual(identify_traits["plan"], "free")
        self.assertEqual(identify_traits["user_flags"], ["beta_flag", "example_flag"])
        self.assertEqual(identify_context["ip"], "0")

    @patch("util.analytics.Analytics.identify")
    def test_set_user_flag_syncs_analytics_when_flag_state_changes(self, mock_identify):
        set_user_flag(self.flag, self.user, True)
        mock_identify.assert_called_once_with(self.user.id, {})

        mock_identify.reset_mock()
        set_user_flag(self.flag, self.user, True)
        mock_identify.assert_not_called()

        set_user_flag(self.flag, self.user, False)
        mock_identify.assert_called_once_with(self.user.id, {})


@tag("batch_user_flags")
class DynamicUserFlagAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        self.admin_user = User.objects.create_superuser(
            username="admin-dynamic-flags@example.com",
            email="admin-dynamic-flags@example.com",
            password="password123",
        )
        self.target_user = User.objects.create_user(
            username="target-dynamic-flags@example.com",
            email="target-dynamic-flags@example.com",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            username="other-dynamic-flags@example.com",
            email="other-dynamic-flags@example.com",
            password="password123",
        )
        self.flag = UserFlagDefinition.objects.create(
            slug="example_flag",
            description="This is an example flag meaning blah blah.",
        )
        self.client.force_login(self.admin_user)
        self.user_admin = CustomUserAdmin(User, admin.site)

    def _change_url(self):
        meta = self.target_user._meta
        return reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[self.target_user.pk])

    def _add_url(self):
        meta = self.target_user._meta
        return reverse(f"admin:{meta.app_label}_{meta.model_name}_add")

    def _changelist_url(self):
        meta = self.target_user._meta
        return reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")

    def _bulk_url(self):
        meta = self.target_user._meta
        return reverse(f"admin:{meta.app_label}_{meta.model_name}_bulk_set_flags")

    def test_user_change_form_renders_configured_user_flags(self):
        response = self.client.get(self._change_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Configured User Flags")
        self.assertContains(response, self.flag.slug)
        self.assertContains(response, self.flag.description)
        self.assertContains(
            response,
            f'name="{CustomUserAdminForm.user_flag_field_name(self.flag)}"',
        )

    def test_user_add_form_preserves_stock_useradmin_add_fields(self):
        response = self.client.get(self._add_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="password1"')
        self.assertContains(response, 'name="password2"')
        self.assertNotContains(response, "Configured User Flags")

    def test_save_model_persists_configured_user_flags(self):
        request = self.factory.post(self._change_url())
        request.user = self.admin_user
        form = CustomUserAdminForm(instance=self.target_user)
        flag_field_name = CustomUserAdminForm.user_flag_field_name(self.flag)
        form.cleaned_data = {
            "execution_paused_admin": False,
            "execution_pause_reason_admin": "",
            flag_field_name: True,
        }

        self.user_admin.save_model(request, self.target_user, form, change=True)
        self.assertTrue(has_user_flag(self.flag.slug, self.target_user))

        form.cleaned_data[flag_field_name] = False
        self.user_admin.save_model(request, self.target_user, form, change=True)
        self.assertFalse(has_user_flag(self.flag.slug, self.target_user))

    def test_bulk_set_user_flags_view_updates_listed_users(self):
        response = self.client.post(
            self._bulk_url(),
            data={
                "user_ids": f"{self.target_user.id}\n{self.other_user.id}\nnot-an-id\n999999",
                "flag": str(self.flag.pk),
                "value": "true",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(has_user_flag(self.flag.slug, self.target_user))
        self.assertTrue(has_user_flag(self.flag.slug, self.other_user))
        messages = [message.message for message in response.context["messages"]]
        self.assertIn("Set 'example_flag' to enabled for 2 user(s).", messages)
        self.assertIn("Skipped invalid user ID tokens: not-an-id.", messages)
        self.assertIn("Skipped missing user IDs: 999999.", messages)

    def test_user_changelist_search_supports_flag_true_and_false(self):
        set_user_flag(self.flag, self.target_user, True)

        true_response = self.client.get(self._changelist_url(), {"q": "example_flag=true"})
        false_response = self.client.get(self._changelist_url(), {"q": "example_flag=false"})

        self.assertEqual(true_response.status_code, 200)
        true_change_list = true_response.context.get("cl")
        self.assertIsNotNone(true_change_list)
        self.assertEqual(true_change_list.result_count, 1)
        self.assertEqual([user.id for user in true_change_list.result_list], [self.target_user.id])

        self.assertEqual(false_response.status_code, 200)
        false_change_list = false_response.context.get("cl")
        self.assertIsNotNone(false_change_list)
        self.assertIn(self.other_user.id, [user.id for user in false_change_list.result_list])
        self.assertNotIn(self.target_user.id, [user.id for user in false_change_list.result_list])

    def test_user_changelist_search_supports_uppercase_flag_slug(self):
        uppercase_flag = UserFlagDefinition.objects.create(
            slug="BetaUser",
            description="Uppercase slug user flag.",
        )
        set_user_flag(uppercase_flag, self.target_user, True)

        response = self.client.get(self._changelist_url(), {"q": "BetaUser=true"})

        self.assertEqual(response.status_code, 200)
        change_list = response.context.get("cl")
        self.assertIsNotNone(change_list)
        self.assertIn(self.target_user.id, [user.id for user in change_list.result_list])

    def test_user_changelist_shows_bulk_set_link(self):
        response = self.client.get(self._changelist_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bulk Set User Flags")
        self.assertContains(response, self._bulk_url())
