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

# Controls whether pricing trial CTA buttons show cancellation reassurance beneath the button.
CTA_PRICING_CANCEL_TEXT_UNDER_BTN = "cta_pricing_cancel_text_under_btn"

# Controls whether pricing trial CTA buttons omit the explicit day count.
CTA_START_FREE_TRIAL = "cta_start_free_trial"

# Controls whether immersive signup preview and /pricing use the
# bundled "unlock your agent" CTA copy refresh.
CTA_UNLOCK_AGENT_COPY = "cta_unlock_agent_copy"

# Controls whether pricing trial modals use a softer completion-style title.
CTA_PICK_A_PLAN = "cta_pick_a_plan"

# Controls whether pricing modal trial CTA buttons say "Continue Your Agent".
CTA_CONTINUE_AGENT_BTN = "cta_continue_agent_btn"

# Controls whether pricing trial CTA helper text emphasizes no charge during the trial.
CTA_NO_CHARGE_DURING_TRIAL = "cta_no_charge_during_trial"

# Controls whether personal no-plan users get a built-in starter charter when
# entering immersive new-agent flow without saved draft state.
PERSONAL_AGENT_SIGNUP_STARTER_CHARTER = "personal_agent_signup_starter_charter"

# Controls whether personal no-plan users see the signup preview UI in immersive
# chat instead of the pricing modal / standard composer flow.
PERSONAL_AGENT_SIGNUP_PREVIEW_UI = "personal_agent_signup_preview_ui"

# Controls whether proprietary personal no-plan users can create a limited
# preview agent that pauses after its first reply until signup is completed.
PERSONAL_AGENT_SIGNUP_PREVIEW_PROCESSING_LIMIT = (
    "personal_agent_signup_preview_processing_limit"
)

# Controls whether UserTrialEligibility decisions block trial CTAs and checkout trial periods.
USER_TRIAL_ELIGIBILITY_ENFORCEMENT = "user_trial_eligibility_enforcement"

# Controls whether personal trial eligibility is limited to a user's own prior
# billing or trial history without cross-account abuse matching.
USER_TRIAL_ELIGIBILITY_ENFORCEMENT_ONE_PER_USER = (
    "user_trial_eligibility_enforcement_one_per_user"
)

# Controls whether StartTrial CAPI is skipped when UserTrialEligibility is not eligible.
START_TRIAL_CAPI_TRIAL_ELIGIBILITY_ENFORCEMENT = (
    "start_trial_capi_trial_eligibility_enforcement"
)

# Controls whether StartTrial CAPI is still sent for stored "review" decisions
# when the StartTrial CAPI trial-eligibility policy is enabled.
START_TRIAL_CAPI_SEND_REVIEW = "start_trial_capi_send_review"

# Controls whether StartTrial CAPI is still sent for stored "no_trial" decisions
# when the StartTrial CAPI trial-eligibility policy is enabled.
START_TRIAL_CAPI_SEND_NO_TRIAL = "start_trial_capi_send_no_trial"

# Controls whether AddPaymentInfo CAPI is skipped when UserTrialEligibility is not eligible.
ADD_PAYMENT_INFO_CAPI_TRIAL_ELIGIBILITY_ENFORCEMENT = (
    "add_payment_info_capi_trial_eligibility_enforcement"
)

# Controls whether AddPaymentInfo CAPI is still sent for stored "review" decisions
# when the AddPaymentInfo CAPI trial-eligibility policy is enabled.
ADD_PAYMENT_INFO_CAPI_SEND_REVIEW = "add_payment_info_capi_send_review"

# Controls whether AddPaymentInfo CAPI is still sent for stored "no_trial" decisions
# when the AddPaymentInfo CAPI trial-eligibility policy is enabled.
ADD_PAYMENT_INFO_CAPI_SEND_NO_TRIAL = "add_payment_info_capi_send_no_trial"

# Controls whether CompleteRegistration CAPI is skipped when
# UserTrialEligibility is not eligible.
COMPLETE_REGISTRATION_CAPI_TRIAL_ELIGIBILITY_ENFORCEMENT = (
    "complete_registration_capi_trial_eligibility_enforcement"
)

# Controls whether CompleteRegistration CAPI is still sent for stored "review"
# decisions when the CompleteRegistration CAPI trial-eligibility policy is
# enabled.
COMPLETE_REGISTRATION_CAPI_SEND_REVIEW = "complete_registration_capi_send_review"

# Controls whether CompleteRegistration CAPI is still sent for stored
# "no_trial" decisions when the CompleteRegistration CAPI trial-eligibility
# policy is enabled.
COMPLETE_REGISTRATION_CAPI_SEND_NO_TRIAL = "complete_registration_capi_send_no_trial"

# Controls whether "review" trial eligibility decisions are treated as trial-allowed
# while still blocking explicit "no_trial" decisions.
USER_TRIAL_REVIEW_ALLOWS_TRIAL = "user_trial_review_allows_trial"

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

# iMessage-style simplified chat UI — collapses non-message events into compact pills
SIMPLIFIED_CHAT_UI = "simplified_chat_ui"
SIMPLIFIED_CHAT_DEFAULT_CONVERSATIONAL = "simplified_chat_default_conversational"
