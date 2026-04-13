"""Default code-defined system skill definitions."""

from .registry import SystemSkillDefinition, SystemSkillField


META_ADS_SYSTEM_SKILL = SystemSkillDefinition(
    skill_key="meta_ads_platform",
    name="Meta Ads Platform",
    search_summary="Monitor Meta ad accounts, campaigns, and reporting data.",
    tool_names=("meta_ads",),
    query_aliases=(
        "meta ads",
        "facebook ads",
        "ads manager",
        "meta ads manager",
        "marketing api",
    ),
    required_profile_fields=(
        SystemSkillField(key="META_APP_ID", name="App ID", description="Meta app identifier."),
        SystemSkillField(key="META_APP_SECRET", name="App Secret", description="Meta app secret."),
        SystemSkillField(
            key="META_SYSTEM_USER_TOKEN",
            name="System User Token",
            description="System user token with ads_read access.",
        ),
        SystemSkillField(
            key="META_AD_ACCOUNT_ID",
            name="Ad Account ID",
            description="Default ad account ID, usually starting with act_.",
        ),
    ),
    optional_profile_fields=(
        SystemSkillField(
            key="META_API_VERSION",
            name="API Version",
            description="Marketing API version override.",
            required=False,
            default="v25.0",
        ),
        SystemSkillField(
            key="META_BUSINESS_ID",
            name="Business ID",
            description="Optional business ID for listing owned ad accounts.",
            required=False,
        ),
    ),
    default_values={"META_API_VERSION": "v25.0"},
    setup_instructions=(
        "Create a Meta Business app, add the Marketing API product, capture the App ID and App Secret, "
        "create a system user in Business Settings, generate a system user token with ads_read access, "
        "and copy the default ad account ID. If token generation goes to approval, another business admin "
        "must approve it in Business Settings -> Requests."
    ),
)


DEFAULT_SYSTEM_SKILL_DEFINITIONS = {
    META_ADS_SYSTEM_SKILL.skill_key: META_ADS_SYSTEM_SKILL,
}
