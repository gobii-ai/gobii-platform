from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from api.models import UserFlags


User = get_user_model()


@tag("batch_user_flags")
class UserFlagsTests(TestCase):
    def test_is_vip_false_without_flags(self):
        user = User.objects.create_user(
            username="noflags@example.com",
            email="noflags@example.com",
            password="pw",
        )

        self.assertFalse(user.is_vip)
        self.assertFalse(UserFlags.objects.filter(user=user).exists())

    def test_is_vip_true_with_flags_and_select_related(self):
        user = User.objects.create_user(
            username="vip@example.com",
            email="vip@example.com",
            password="pw",
        )
        UserFlags.objects.create(user=user, is_vip=True)

        user.refresh_from_db()
        self.assertTrue(user.is_vip)

        fetched = User.objects.select_related("flags").get(id=user.id)
        self.assertTrue(fetched.is_vip)

    def test_is_freemium_grandfathered_false_without_flags(self):
        user = User.objects.create_user(
            username="nograndfather@example.com",
            email="nograndfather@example.com",
            password="pw",
        )

        self.assertFalse(user.is_freemium_grandfathered)
        self.assertFalse(UserFlags.objects.filter(user=user).exists())

    def test_is_freemium_grandfathered_true_with_flags(self):
        user = User.objects.create_user(
            username="grandfathered@example.com",
            email="grandfathered@example.com",
            password="pw",
        )
        UserFlags.objects.create(user=user, is_freemium_grandfathered=True)

        user.refresh_from_db()
        self.assertTrue(user.is_freemium_grandfathered)

        fetched = User.objects.select_related("flags").get(id=user.id)
        self.assertTrue(fetched.is_freemium_grandfathered)

    def test_ensure_for_user_creates_flags(self):
        user = User.objects.create_user(
            username="ensure@example.com",
            email="ensure@example.com",
            password="pw",
        )

        flags = UserFlags.ensure_for_user(user)
        self.assertTrue(flags)
        self.assertEqual(flags.user, user)
        self.assertFalse(flags.is_vip)
        self.assertFalse(flags.is_freemium_grandfathered)
