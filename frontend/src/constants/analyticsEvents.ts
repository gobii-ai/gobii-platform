export const AnalyticsEvent = {
  INSIGHT_DISMISSED: 'Insight Dismissed',
  INSIGHT_TAB_CLICKED: 'Insight Tab Clicked',
  INSIGHT_PANEL_TOGGLED: 'Insight Panel Toggled',

  INSIGHT_TIME_SAVED_CLICKED: 'Insight - Time Saved Clicked',
  INSIGHT_BURN_RATE_CLICKED: 'Insight - Burn Rate Clicked',
  INSIGHT_UPGRADE_CLICKED: 'Insight - Upgrade Clicked',

  AGENT_SETUP_SMS_CODE_SENT: 'Agent - SMS - Code Sent',
  AGENT_SETUP_SMS_VERIFIED: 'Agent - SMS - Verified',
  AGENT_SETUP_SMS_ENABLED: 'Agent - SMS - Enabled',
  AGENT_SETUP_SMS_NUMBER_COPIED: 'Agent - SMS Copied',

  AGENT_SETUP_ORG_MOVED: 'Agent - Org Move',

  AGENT_SETUP_UPGRADE_CLICKED: 'Agent - Upgrade Clicked',

  SIGNUP_PREVIEW_ENTERED: 'Signup Preview Entered',
  SIGNUP_PREVIEW_AGENT_CREATED: 'Signup Preview Agent Created',
  SIGNUP_PREVIEW_PAUSED_AFTER_FIRST_REPLY: 'Signup Preview Paused After First Reply',
  SIGNUP_PREVIEW_RESUMED_AFTER_PLAN: 'Signup Preview Resumed After Plan',
  SIGNUP_PREVIEW_ACTION_BLOCKED: 'Signup Preview Action Blocked',
  SIGNUP_PREVIEW_CLOSED: 'Signup Preview Closed',
  SIGNUP_PREVIEW_COMPARISON_CLICKED: 'Signup Preview Comparison Clicked',
  SIGNUP_PREVIEW_FEATURES_TOGGLED: 'Signup Preview Features Toggled',
  SIGNUP_PREVIEW_CONTACT_CLICKED: 'Signup Preview Contact Clicked',

  UPGRADE_BANNER_CLICKED: 'Upgrade Banner Clicked',
  UPGRADE_MODAL_OPENED: 'Upgrade Modal Opened',
  UPGRADE_MODAL_DISMISSED: 'Upgrade Modal Dismissed',
  UPGRADE_PLAN_SELECTED: 'Upgrade Plan Selected',
  UPGRADE_CHECKOUT_REDIRECTED: 'Upgrade Checkout Redirected',
  CTA_FREE_UPGRADE_PLAN: 'CTA - Free - Upgrade Plan',

  BILLING_CANCEL_FLOW_OPENED: 'Billing Cancel Flow Opened',
  BILLING_CANCEL_FLOW_ACTION_SELECTED: 'Billing Cancel Flow Action Selected',
  BILLING_CANCEL_FLOW_GO_TO_ACCOUNT: 'Billing Cancel Flow Go To Account',
  BILLING_CANCEL_FLOW_CLOSED: 'Billing Cancel Flow Closed',
  BILLING_CANCEL_FLOW_ERROR: 'Billing Cancel Flow Error',

  INTELLIGENCE_GATE_SHOWN: 'Intelligence Gate Shown',
  INTELLIGENCE_GATE_DISMISSED: 'Intelligence Gate Dismissed',
  INTELLIGENCE_GATE_CONTINUED: 'Intelligence Gate Continued',
  INTELLIGENCE_GATE_ADD_PACK_CLICKED: 'Intelligence Gate Add Pack Clicked',

  AGENT_CHAT_STARTER_PROMPT_CLICKED: 'Agent Chat Starter Prompt Clicked',
  UPSELL_MESSAGE_SHOWN: 'Upsell Message Shown',
  UPSELL_MESSAGE_DISMISSED: 'Upsell Message Dismissed',

  HUMAN_INPUT_PANEL_SHOWN: 'Human Input Panel Shown',
  HUMAN_INPUT_OPTION_SELECTED: 'Human Input Option Selected',

  NOTIFICATION_BELL_OPENED: 'Notification Bell Opened',
  NOTIFICATION_BELL_ITEM_OPENED: 'Notification Bell Item Opened',
  NOTIFICATION_BELL_ACTION_CLICKED: 'Notification Bell Action Clicked',
  NOTIFICATION_BELL_ITEM_MARK_READ_CLICKED: 'Notification Bell Item Mark Read Clicked',
  NOTIFICATION_BELL_MARK_ALL_READ_CLICKED: 'Notification Bell Mark All Read Clicked',

  CTA_CLICKED: 'CTA Clicked',
} as const

export type AnalyticsEventType = typeof AnalyticsEvent[keyof typeof AnalyticsEvent]
