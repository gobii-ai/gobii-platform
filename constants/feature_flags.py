# constants/feature_flags.py
PERSISTENT_AGENTS = "persistent_agents"
ORGANIZATIONS = "organizations"
MULTIPLAYER_AGENTS = "multiplayer_agents"

# Soft-expiration for free-plan agents that go inactive
AGENT_SOFT_EXPIRATION = "agent_soft_expiration"

# Exponential backoff for cron-triggered runs on free-plan agents
AGENT_CRON_THROTTLE = "agent_cron_throttle"

# Route /support form submissions to Intercom-style email intake.
SUPPORT_INTERCOM = "support_intercom"


# Controls favicon/logo collateral assets across templates and app shell
FISH_COLLATERAL = "fish_collateral"

# Controls whether the pricing upgrade modal renders in an almost full-screen layout.
PRICING_MODAL_ALMOST_FULL_SCREEN = "pricing_modal_almost_full_screen"

# Controls whether pricing trial CTA buttons omit the explicit day count.
CTA_START_FREE_TRIAL = "cta_start_free_trial"



# Are we allow to send to multiple comm points at once - NOTE THIS IS NOT THE SAME AS MULTIPLAYER_AGENTS
# This is a switch to send to multiple comms points at once, such as email and sms, or multiple emails. has to be a
# switch not flag
MULTISEND_ENABLED = "multisend_enabled"

# Retry one completion when web chat session becomes active mid-iteration.
AGENT_RETRY_COMPLETION_ON_WEB_SESSION_ACTIVATION = (
    "agent_retry_completion_on_web_session_activation"
)

# Owner-wide execution pause controls for billing lifecycle events.
OWNER_EXECUTION_PAUSE_ON_BILLING_DELINQUENCY = (
    "owner_execution_pause_on_billing_delinquency"
)
OWNER_EXECUTION_PAUSE_ON_TRIAL_CONVERSION_FAILED = (
    "owner_execution_pause_on_trial_conversion_failed"
)
