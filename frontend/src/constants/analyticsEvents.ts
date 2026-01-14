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
  INSIGHT_TIME_SAVED_CLICKED: 'Insight Time Saved Clicked',
  /** User clicked anywhere on the burn rate insight */
  INSIGHT_BURN_RATE_CLICKED: 'Insight Burn Rate Clicked',
  /** User clicked the upgrade CTA in an upsell insight */
  INSIGHT_UPGRADE_CLICKED: 'Insight Upgrade Clicked',

  // ============================================
  // Agent Setup - SMS Flow
  // ============================================
  /** User clicked "Send Code" to start SMS verification */
  AGENT_SETUP_SMS_CODE_SENT: 'Agent Setup SMS Code Sent',
  /** User successfully verified their phone */
  AGENT_SETUP_SMS_VERIFIED: 'Agent Setup SMS Verified',
  /** User clicked "Enable SMS" to connect agent */
  AGENT_SETUP_SMS_ENABLED: 'Agent Setup SMS Enabled',
  /** User copied the agent's SMS number */
  AGENT_SETUP_SMS_NUMBER_COPIED: 'Agent Setup SMS Number Copied',

  // ============================================
  // Agent Setup - Organization
  // ============================================
  /** User moved agent to a different org */
  AGENT_SETUP_ORG_MOVED: 'Agent Setup Org Moved',

  // ============================================
  // Agent Setup - Upsell
  // ============================================
  /** User clicked upgrade CTA from agent setup panel */
  AGENT_SETUP_UPGRADE_CLICKED: 'Agent Setup Upgrade Clicked',
} as const

export type AnalyticsEventType = typeof AnalyticsEvent[keyof typeof AnalyticsEvent]
