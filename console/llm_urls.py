from django.urls import path

from console import api_views as v


def _tier_urls(prefix, name_prefix, endpoint_list, endpoint_detail, tier_list, tier_detail, tier_endpoint_list, tier_endpoint_detail):
    return [
        path(f"{prefix}/endpoints/", endpoint_list.as_view(), name=f"{name_prefix}_endpoints"),
        path(f"{prefix}/endpoints/<uuid:endpoint_id>/", endpoint_detail.as_view(), name=f"{name_prefix}_endpoint_detail"),
        path(f"{prefix}/tiers/", tier_list.as_view(), name=f"{name_prefix}_tiers"),
        path(f"{prefix}/tiers/<uuid:tier_id>/", tier_detail.as_view(), name=f"{name_prefix}_tier_detail"),
        path(f"{prefix}/tiers/<uuid:tier_id>/endpoints/", tier_endpoint_list.as_view(), name=f"{name_prefix}_tier_endpoints"),
        path(f"{prefix}/tier-endpoints/<uuid:tier_endpoint_id>/", tier_endpoint_detail.as_view(), name=f"{name_prefix}_tier_endpoint_detail"),
    ]


urlpatterns = [
    path(route, view.as_view(), name=name)
    for route, view, name in (
        ("overview/", v.ConsoleLLMOverviewAPIView, "console_llm_overview"),
        ("providers/", v.LLMProviderListCreateAPIView, "console_llm_providers"),
        ("providers/<uuid:provider_id>/", v.LLMProviderDetailAPIView, "console_llm_provider_detail"),
        ("test-endpoint/", v.LLMEndpointTestAPIView, "console_llm_test_endpoint"),
        ("persistent/endpoints/", v.PersistentEndpointListCreateAPIView, "console_llm_persistent_endpoints"),
        ("persistent/endpoints/<uuid:endpoint_id>/", v.PersistentEndpointDetailAPIView, "console_llm_persistent_endpoint_detail"),
        ("persistent/ranges/", v.PersistentTokenRangeListCreateAPIView, "console_llm_ranges"),
        ("persistent/ranges/<uuid:range_id>/", v.PersistentTokenRangeDetailAPIView, "console_llm_range_detail"),
        ("persistent/ranges/<uuid:range_id>/tiers/", v.PersistentTierListCreateAPIView, "console_llm_range_tiers"),
        ("persistent/tiers/<uuid:tier_id>/", v.PersistentTierDetailAPIView, "console_llm_tier_detail"),
        ("persistent/tiers/<uuid:tier_id>/endpoints/", v.PersistentTierEndpointListCreateAPIView, "console_llm_tier_endpoints"),
        ("persistent/tier-endpoints/<uuid:tier_endpoint_id>/", v.PersistentTierEndpointDetailAPIView, "console_llm_tier_endpoint_detail"),
        ("browser/endpoints/", v.BrowserEndpointListCreateAPIView, "console_llm_browser_endpoints"),
        ("browser/endpoints/<uuid:endpoint_id>/", v.BrowserEndpointDetailAPIView, "console_llm_browser_endpoint_detail"),
        ("browser/tiers/", v.BrowserTierListCreateAPIView, "console_llm_browser_tiers"),
        ("browser/tiers/<uuid:tier_id>/", v.BrowserTierDetailAPIView, "console_llm_browser_tier_detail"),
        ("browser/tiers/<uuid:tier_id>/endpoints/", v.BrowserTierEndpointListCreateAPIView, "console_llm_browser_tier_endpoints"),
        ("browser/tier-endpoints/<uuid:tier_endpoint_id>/", v.BrowserTierEndpointDetailAPIView, "console_llm_browser_tier_endpoint_detail"),
        ("routing-profiles/", v.LLMRoutingProfileListCreateAPIView, "console_llm_routing_profiles"),
        ("routing-profiles/<uuid:profile_id>/", v.LLMRoutingProfileDetailAPIView, "console_llm_routing_profile_detail"),
        ("routing-profiles/<uuid:profile_id>/activate/", v.LLMRoutingProfileActivateAPIView, "console_llm_routing_profile_activate"),
        ("routing-profiles/<uuid:profile_id>/clone/", v.LLMRoutingProfileCloneAPIView, "console_llm_routing_profile_clone"),
        ("routing-profiles/<uuid:profile_id>/token-ranges/", v.ProfileTokenRangeListCreateAPIView, "console_llm_profile_token_ranges"),
        ("routing-profiles/token-ranges/<uuid:range_id>/", v.ProfileTokenRangeDetailAPIView, "console_llm_profile_token_range_detail"),
        ("routing-profiles/token-ranges/<uuid:range_id>/tiers/", v.ProfilePersistentTierListCreateAPIView, "console_llm_profile_persistent_tiers"),
        ("routing-profiles/persistent-tiers/<uuid:tier_id>/", v.ProfilePersistentTierDetailAPIView, "console_llm_profile_persistent_tier_detail"),
        ("routing-profiles/persistent-tiers/<uuid:tier_id>/endpoints/", v.ProfilePersistentTierEndpointListCreateAPIView, "console_llm_profile_persistent_tier_endpoints"),
        ("routing-profiles/persistent-tier-endpoints/<uuid:tier_endpoint_id>/", v.ProfilePersistentTierEndpointDetailAPIView, "console_llm_profile_persistent_tier_endpoint_detail"),
        ("routing-profiles/<uuid:profile_id>/browser-tiers/", v.ProfileBrowserTierListCreateAPIView, "console_llm_profile_browser_tiers"),
        ("routing-profiles/browser-tiers/<uuid:tier_id>/", v.ProfileBrowserTierDetailAPIView, "console_llm_profile_browser_tier_detail"),
        ("routing-profiles/browser-tiers/<uuid:tier_id>/endpoints/", v.ProfileBrowserTierEndpointListCreateAPIView, "console_llm_profile_browser_tier_endpoints"),
        ("routing-profiles/browser-tier-endpoints/<uuid:tier_endpoint_id>/", v.ProfileBrowserTierEndpointDetailAPIView, "console_llm_profile_browser_tier_endpoint_detail"),
        ("routing-profiles/<uuid:profile_id>/embeddings-tiers/", v.ProfileEmbeddingsTierListCreateAPIView, "console_llm_profile_embeddings_tiers"),
        ("routing-profiles/embeddings-tiers/<uuid:tier_id>/", v.ProfileEmbeddingsTierDetailAPIView, "console_llm_profile_embeddings_tier_detail"),
        ("routing-profiles/embeddings-tiers/<uuid:tier_id>/endpoints/", v.ProfileEmbeddingsTierEndpointListCreateAPIView, "console_llm_profile_embeddings_tier_endpoints"),
        ("routing-profiles/embeddings-tier-endpoints/<uuid:tier_endpoint_id>/", v.ProfileEmbeddingsTierEndpointDetailAPIView, "console_llm_profile_embeddings_tier_endpoint_detail"),
    )
]

for _prefix, _name_prefix, _views in (
    ("embeddings", "console_llm_embedding", (v.EmbeddingEndpointListCreateAPIView, v.EmbeddingEndpointDetailAPIView, v.EmbeddingTierListCreateAPIView, v.EmbeddingTierDetailAPIView, v.EmbeddingTierEndpointListCreateAPIView, v.EmbeddingTierEndpointDetailAPIView)),
    ("file-handlers", "console_llm_file_handler", (v.FileHandlerEndpointListCreateAPIView, v.FileHandlerEndpointDetailAPIView, v.FileHandlerTierListCreateAPIView, v.FileHandlerTierDetailAPIView, v.FileHandlerTierEndpointListCreateAPIView, v.FileHandlerTierEndpointDetailAPIView)),
    ("image-generations", "console_llm_image_generation", (v.ImageGenerationEndpointListCreateAPIView, v.ImageGenerationEndpointDetailAPIView, v.ImageGenerationTierListCreateAPIView, v.ImageGenerationTierDetailAPIView, v.ImageGenerationTierEndpointListCreateAPIView, v.ImageGenerationTierEndpointDetailAPIView)),
    ("video-generations", "console_llm_video_generation", (v.VideoGenerationEndpointListCreateAPIView, v.VideoGenerationEndpointDetailAPIView, v.VideoGenerationTierListCreateAPIView, v.VideoGenerationTierDetailAPIView, v.VideoGenerationTierEndpointListCreateAPIView, v.VideoGenerationTierEndpointDetailAPIView)),
):
    urlpatterns += _tier_urls(_prefix, _name_prefix, *_views)
del _prefix, _name_prefix, _views
