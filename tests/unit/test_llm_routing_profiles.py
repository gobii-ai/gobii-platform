"""Tests for LLM routing profile functionality."""

from decimal import Decimal
from django.test import TestCase, TransactionTestCase, tag
from django.contrib.auth import get_user_model

from api.models import (
    LLMProvider,
    LLMRoutingProfile,
    PersistentModelEndpoint,
    BrowserModelEndpoint,
    EmbeddingsModelEndpoint,
    ProfileTokenRange,
    ProfilePersistentTier,
    ProfilePersistentTierEndpoint,
    ProfileBrowserTier,
    ProfileBrowserTierEndpoint,
    ProfileEmbeddingsTier,
    ProfileEmbeddingsTierEndpoint,
)


User = get_user_model()


@tag("llm_routing_profiles_batch")
class LLMRoutingProfileModelTests(TestCase):
    """Tests for the LLMRoutingProfile model and its related models."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
        )
        cls.provider = LLMProvider.objects.create(
            key="test-provider",
            display_name="Test Provider",
            enabled=True,
        )
        cls.persistent_endpoint = PersistentModelEndpoint.objects.create(
            key="test-persistent",
            provider=cls.provider,
            litellm_model="gpt-4",
            enabled=True,
        )
        cls.browser_endpoint = BrowserModelEndpoint.objects.create(
            key="test-browser",
            provider=cls.provider,
            browser_model="gpt-4",
            enabled=True,
        )
        cls.embedding_endpoint = EmbeddingsModelEndpoint.objects.create(
            key="test-embedding",
            provider=cls.provider,
            litellm_model="text-embedding-3-small",
            enabled=True,
        )

    def test_create_routing_profile(self):
        """Test basic profile creation."""
        profile = LLMRoutingProfile.objects.create(
            name="test-profile",
            display_name="Test Profile",
            description="A test profile",
            is_active=False,
            created_by=self.user,
        )
        self.assertEqual(profile.name, "test-profile")
        self.assertEqual(profile.display_name, "Test Profile")
        self.assertFalse(profile.is_active)
        self.assertEqual(profile.created_by, self.user)

    def test_profile_name_uniqueness(self):
        """Test that profile names must be unique."""
        LLMRoutingProfile.objects.create(name="unique-name", display_name="First")
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            LLMRoutingProfile.objects.create(name="unique-name", display_name="Second")

    def test_create_profile_with_token_range(self):
        """Test creating a profile with token ranges."""
        profile = LLMRoutingProfile.objects.create(
            name="profile-with-ranges",
            display_name="Profile with Ranges",
        )
        token_range = ProfileTokenRange.objects.create(
            profile=profile,
            name="small",
            min_tokens=0,
            max_tokens=4096,
        )
        self.assertEqual(token_range.profile, profile)
        self.assertEqual(token_range.min_tokens, 0)
        self.assertEqual(token_range.max_tokens, 4096)

    def test_create_persistent_tier_with_endpoints(self):
        """Test creating persistent tiers with endpoints."""
        profile = LLMRoutingProfile.objects.create(
            name="profile-persistent",
            display_name="Profile Persistent",
        )
        token_range = ProfileTokenRange.objects.create(
            profile=profile,
            name="default",
            min_tokens=0,
        )
        tier = ProfilePersistentTier.objects.create(
            token_range=token_range,
            order=1,
            description="Primary tier",
            is_premium=False,
            is_max=False,
            credit_multiplier=Decimal("1.0"),
        )
        tier_endpoint = ProfilePersistentTierEndpoint.objects.create(
            tier=tier,
            endpoint=self.persistent_endpoint,
            weight=1.0,
        )
        self.assertEqual(tier_endpoint.tier, tier)
        self.assertEqual(tier_endpoint.endpoint, self.persistent_endpoint)
        self.assertEqual(tier_endpoint.weight, 1.0)

    def test_create_browser_tier_with_endpoints(self):
        """Test creating browser tiers with endpoints."""
        profile = LLMRoutingProfile.objects.create(
            name="profile-browser",
            display_name="Profile Browser",
        )
        tier = ProfileBrowserTier.objects.create(
            profile=profile,
            order=1,
            description="Browser tier",
            is_premium=False,
        )
        tier_endpoint = ProfileBrowserTierEndpoint.objects.create(
            tier=tier,
            endpoint=self.browser_endpoint,
            weight=1.0,
        )
        self.assertEqual(tier_endpoint.tier, tier)
        self.assertEqual(tier_endpoint.endpoint, self.browser_endpoint)

    def test_create_embeddings_tier_with_endpoints(self):
        """Test creating embeddings tiers with endpoints."""
        profile = LLMRoutingProfile.objects.create(
            name="profile-embeddings",
            display_name="Profile Embeddings",
        )
        tier = ProfileEmbeddingsTier.objects.create(
            profile=profile,
            order=1,
            description="Embeddings tier",
        )
        tier_endpoint = ProfileEmbeddingsTierEndpoint.objects.create(
            tier=tier,
            endpoint=self.embedding_endpoint,
            weight=1.0,
        )
        self.assertEqual(tier_endpoint.tier, tier)
        self.assertEqual(tier_endpoint.endpoint, self.embedding_endpoint)

    def test_cascade_delete_profile(self):
        """Test that deleting a profile cascades to all related objects."""
        profile = LLMRoutingProfile.objects.create(
            name="cascade-test",
            display_name="Cascade Test",
        )
        token_range = ProfileTokenRange.objects.create(
            profile=profile,
            name="range",
            min_tokens=0,
        )
        tier = ProfilePersistentTier.objects.create(
            token_range=token_range,
            order=1,
        )
        ProfilePersistentTierEndpoint.objects.create(
            tier=tier,
            endpoint=self.persistent_endpoint,
            weight=1.0,
        )
        browser_tier = ProfileBrowserTier.objects.create(
            profile=profile,
            order=1,
        )
        ProfileBrowserTierEndpoint.objects.create(
            tier=browser_tier,
            endpoint=self.browser_endpoint,
            weight=1.0,
        )

        profile_id = profile.id
        profile.delete()

        # Verify all related objects are deleted
        self.assertFalse(ProfileTokenRange.objects.filter(profile_id=profile_id).exists())
        self.assertFalse(ProfilePersistentTier.objects.filter(token_range__profile_id=profile_id).exists())
        self.assertFalse(ProfileBrowserTier.objects.filter(profile_id=profile_id).exists())

    def test_cloned_from_relationship(self):
        """Test the cloned_from self-referential relationship."""
        original = LLMRoutingProfile.objects.create(
            name="original",
            display_name="Original",
        )
        clone = LLMRoutingProfile.objects.create(
            name="clone",
            display_name="Clone",
            cloned_from=original,
        )
        self.assertEqual(clone.cloned_from, original)
        self.assertIn(clone, original.clones.all())


@tag("llm_routing_profiles_batch")
class LLMRoutingProfileSerializerTests(TestCase):
    """Tests for the routing profile serialization functions."""

    @classmethod
    def setUpTestData(cls):
        cls.provider = LLMProvider.objects.create(
            key="serializer-test-provider",
            display_name="Serializer Test Provider",
            enabled=True,
        )
        cls.persistent_endpoint = PersistentModelEndpoint.objects.create(
            key="serializer-persistent",
            provider=cls.provider,
            litellm_model="gpt-4",
            enabled=True,
        )
        cls.browser_endpoint = BrowserModelEndpoint.objects.create(
            key="serializer-browser",
            provider=cls.provider,
            browser_model="gpt-4",
            enabled=True,
        )
        cls.embedding_endpoint = EmbeddingsModelEndpoint.objects.create(
            key="serializer-embedding",
            provider=cls.provider,
            litellm_model="text-embedding-3-small",
            enabled=True,
        )

    def _create_full_profile(self, name):
        """Helper to create a profile with all nested config."""
        profile = LLMRoutingProfile.objects.create(
            name=name,
            display_name=f"{name} Display",
            description=f"Description for {name}",
            is_active=False,
        )

        # Persistent config
        token_range = ProfileTokenRange.objects.create(
            profile=profile,
            name="default",
            min_tokens=0,
            max_tokens=8192,
        )
        tier = ProfilePersistentTier.objects.create(
            token_range=token_range,
            order=1,
            description="Primary",
            credit_multiplier=Decimal("1.5"),
        )
        ProfilePersistentTierEndpoint.objects.create(
            tier=tier,
            endpoint=self.persistent_endpoint,
            weight=1.0,
        )

        # Browser config
        browser_tier = ProfileBrowserTier.objects.create(
            profile=profile,
            order=1,
            description="Browser primary",
        )
        ProfileBrowserTierEndpoint.objects.create(
            tier=browser_tier,
            endpoint=self.browser_endpoint,
            weight=1.0,
        )

        # Embeddings config
        embedding_tier = ProfileEmbeddingsTier.objects.create(
            profile=profile,
            order=1,
            description="Embedding primary",
        )
        ProfileEmbeddingsTierEndpoint.objects.create(
            tier=embedding_tier,
            endpoint=self.embedding_endpoint,
            weight=1.0,
        )

        return profile

    def test_serialize_profile_list_item(self):
        """Test serializing a profile for list views."""
        from console.llm_serializers import serialize_routing_profile_list_item

        profile = self._create_full_profile("list-item-test")
        data = serialize_routing_profile_list_item(profile)

        self.assertEqual(data["id"], str(profile.id))
        self.assertEqual(data["name"], "list-item-test")
        self.assertEqual(data["display_name"], "list-item-test Display")
        self.assertFalse(data["is_active"])
        self.assertIn("created_at", data)
        self.assertIn("updated_at", data)

    def test_serialize_profile_detail(self):
        """Test serializing a full profile with nested config."""
        from console.llm_serializers import (
            get_routing_profile_with_prefetch,
            serialize_routing_profile_detail,
        )

        profile = self._create_full_profile("detail-test")
        prefetched = get_routing_profile_with_prefetch(str(profile.id))
        data = serialize_routing_profile_detail(prefetched)

        self.assertEqual(data["id"], str(profile.id))
        self.assertEqual(data["name"], "detail-test")

        # Check persistent config
        self.assertIn("persistent", data)
        self.assertEqual(len(data["persistent"]["ranges"]), 1)
        token_range = data["persistent"]["ranges"][0]
        self.assertEqual(token_range["name"], "default")
        self.assertEqual(len(token_range["tiers"]), 1)
        self.assertEqual(len(token_range["tiers"][0]["endpoints"]), 1)

        # Check browser config
        self.assertIn("browser", data)
        self.assertEqual(len(data["browser"]["tiers"]), 1)
        self.assertEqual(len(data["browser"]["tiers"][0]["endpoints"]), 1)

        # Check embeddings config
        self.assertIn("embeddings", data)
        self.assertEqual(len(data["embeddings"]["tiers"]), 1)
        self.assertEqual(len(data["embeddings"]["tiers"][0]["endpoints"]), 1)

    def test_build_routing_profiles_list(self):
        """Test building the profiles list."""
        from console.llm_serializers import build_routing_profiles_list

        self._create_full_profile("list-test-1")
        self._create_full_profile("list-test-2")

        profiles = build_routing_profiles_list()
        names = [p["name"] for p in profiles]

        self.assertIn("list-test-1", names)
        self.assertIn("list-test-2", names)


@tag("llm_routing_profiles_batch")
class LLMRoutingProfileActivationTests(TransactionTestCase):
    """Tests for profile activation logic."""

    def test_activate_profile_deactivates_others(self):
        """Test that activating a profile deactivates all others."""
        profile1 = LLMRoutingProfile.objects.create(
            name="profile1",
            display_name="Profile 1",
            is_active=True,
        )
        profile2 = LLMRoutingProfile.objects.create(
            name="profile2",
            display_name="Profile 2",
            is_active=False,
        )

        # Activate profile2
        LLMRoutingProfile.objects.exclude(pk=profile2.id).update(is_active=False)
        profile2.is_active = True
        profile2.save()

        profile1.refresh_from_db()
        profile2.refresh_from_db()

        self.assertFalse(profile1.is_active)
        self.assertTrue(profile2.is_active)

    def test_only_one_active_profile_enforced_by_db(self):
        """Test that the database enforces only one active profile."""
        from django.db import IntegrityError

        LLMRoutingProfile.objects.create(
            name="active1",
            display_name="Active 1",
            is_active=True,
        )

        # Attempting to create another active profile should raise IntegrityError
        with self.assertRaises(IntegrityError):
            LLMRoutingProfile.objects.create(
                name="active2",
                display_name="Active 2",
                is_active=True,
            )

    def test_multiple_inactive_profiles_allowed(self):
        """Test that multiple inactive profiles are allowed."""
        LLMRoutingProfile.objects.create(
            name="inactive1",
            display_name="Inactive 1",
            is_active=False,
        )
        LLMRoutingProfile.objects.create(
            name="inactive2",
            display_name="Inactive 2",
            is_active=False,
        )

        # Both should exist
        inactive_count = LLMRoutingProfile.objects.filter(is_active=False).count()
        self.assertEqual(inactive_count, 2)
