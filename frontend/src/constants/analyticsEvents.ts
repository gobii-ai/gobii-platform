/**
 * Analytics event constants for the React frontend.
 *
 * These mirror a subset of the Python AnalyticsEvent enum (util/analytics.py).
 * Only include events that are tracked from the frontend.
 *
 * Naming convention: Use Title Case for event names (matches Segment/Mixpanel convention)
 */

export const AnalyticsEvent = {
  // ============================================
  // Insights Panel - General
  // ============================================
  /** User dismissed an insight via X button */
  INSIGHT_DISMISSED: 'Insight Dismissed',
  /** User clicked a tab in the insight carousel */
  INSIGHT_TAB_CLICKED: 'Insight Tab Clicked',
  /** User expanded/collapsed the insights panel */
  INSIGHT_PANEL_TOGGLED: 'Insight Panel Toggled',

  // ============================================
  // Insights Panel - Specific Insight Interactions
  // ============================================
  /** User clicked anywhere on the time saved insight */
  INSIGHT_TIME_SAVED_CLICKED: 'Insight - Time Saved Clicked',
  /** User clicked anywhere on the burn rate insight */
  INSIGHT_BURN_RATE_CLICKED: 'Insight - Burn Rate Clicked',
  /** User clicked the upgrade CTA in an upsell insight */
  INSIGHT_UPGRADE_CLICKED: 'Insight - Upgrade Clicked',

  // ============================================
  // Agent Setup - SMS Flow
  // ============================================
  /** User clicked "Send Code" to start SMS verification */
  AGENT_SETUP_SMS_CODE_SENT: 'Agent - SMS - Code Sent',
  /** User successfully verified their phone */
  AGENT_SETUP_SMS_VERIFIED: 'Agent - SMS - Verified',
  /** User clicked "Enable SMS" to connect agent */
  AGENT_SETUP_SMS_ENABLED: 'Agent - SMS - Enabled',
  /** User copied the agent's SMS number */
  AGENT_SETUP_SMS_NUMBER_COPIED: 'Agent - SMS Copied',

  // ============================================
  // Agent Setup - Organization
  // ============================================
  /** User moved agent to a different org */
  AGENT_SETUP_ORG_MOVED: 'Agent - Org Move',

  // ============================================
  // Agent Setup - Upsell
  // ============================================
  /** User clicked upgrade CTA from agent setup panel */
  AGENT_SETUP_UPGRADE_CLICKED: 'Agent - Upgrade Clicked',

  // ============================================
  // Subscription Upgrade Flow
  // ============================================
  /** User clicked upgrade button in banner */
  UPGRADE_BANNER_CLICKED: 'Upgrade Banner Clicked',
  /** Upgrade modal was opened */
  UPGRADE_MODAL_OPENED: 'Upgrade Modal Opened',
  /** User dismissed the upgrade modal */
  UPGRADE_MODAL_DISMISSED: 'Upgrade Modal Dismissed',
  /** User clicked upgrade CTA for a specific plan in modal */
  UPGRADE_PLAN_SELECTED: 'Upgrade Plan Selected',
  /** User was redirected to checkout for an upgrade */
  UPGRADE_CHECKOUT_REDIRECTED: 'Upgrade Checkout Redirected',
  /** Free user clicked upgrade CTA from billing page */
  CTA_FREE_UPGRADE_PLAN: 'CTA - Free - Upgrade Plan',

  // ============================================
  // Intelligence Gate Events
  // ============================================
  /** Intelligence gate modal was shown */
  INTELLIGENCE_GATE_SHOWN: 'Intelligence Gate Shown',
  /** User dismissed the intelligence gate */
  INTELLIGENCE_GATE_DISMISSED: 'Intelligence Gate Dismissed',
  /** User continued with a lower intelligence tier */
  INTELLIGENCE_GATE_CONTINUED: 'Intelligence Gate Continued',
  /** User clicked add pack from the intelligence gate */
  INTELLIGENCE_GATE_ADD_PACK_CLICKED: 'Intelligence Gate Add Pack Clicked',

  // ============================================
  // Upsell Message Events
  // ============================================
  /** User clicked a starter prompt in agent live chat */
  AGENT_CHAT_STARTER_PROMPT_CLICKED: 'Agent Chat Starter Prompt Clicked',
  /** Upsell message was shown to the user */
  UPSELL_MESSAGE_SHOWN: 'Upsell Message Shown',
  /** User dismissed an upsell message */
  UPSELL_MESSAGE_DISMISSED: 'Upsell Message Dismissed',


  // ============================================
  // CTA Click - for tracking clicks of CTAs; for our funnel/flow analytics
  // ============================================
  CTA_CLICKED: 'CTA Clicked',
} as const

export type AnalyticsEventType = typeof AnalyticsEvent[keyof typeof AnalyticsEvent]
