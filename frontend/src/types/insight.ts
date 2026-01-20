/**
 * Insight types for the agent working state.
 * Insights are contextual, helpful information shown inline during processing.
 */

export type InsightType = 'time_saved' | 'burn_rate' | 'agent_setup'

// Timing constants for insight display
export const INSIGHT_TIMING = {
  showAfterMs: 800, // Delay before first insight appears
  rotationIntervalMs: 10000, // Time between rotations
  fadeInMs: 300, // Fade in duration
  fadeOutMs: 200, // Fade out duration
  minProcessingMs: 3000, // Don't show insights if processing < 3s
} as const

// Type-specific metadata shapes

export type TimeSavedMetadata = {
  hoursSaved: number
  tasksCompleted: number
  comparisonPeriod: 'week' | 'month' | 'all_time'
  methodology: string
}

export type BurnRateMetadata = {
  agentName: string
  agentCreditsPerHour: number
  allAgentsCreditsPerDay: number
  dailyLimit: number
  percentUsed: number
}

export type AgentSetupPanel = 'always_on' | 'sms' | 'org_transfer' | 'upsell_pro' | 'upsell_scale' | 'template'

export type AgentSetupPhone = {
  number: string
  isVerified: boolean
  verifiedAt: string | null
  cooldownRemaining: number
}

export type AgentSetupUpsellItem = {
  plan: 'pro' | 'scale'
  title: string
  subtitle: string
  body: string
  bullets: string[]
  price?: string | null
  ctaLabel: string
  accent: 'indigo' | 'violet'
}

export type AgentSetupMetadata = {
  agentId: string
  panel?: AgentSetupPanel
  alwaysOn: {
    title: string
    body: string
    note?: string | null
  }
  sms: {
    enabled: boolean
    agentNumber?: string | null
    userPhone?: AgentSetupPhone | null
    emailVerified?: boolean
  }
  organization: {
    currentOrg?: { id: string; name: string } | null
    options: { id: string; name: string }[]
  }
  upsell?: {
    items: AgentSetupUpsellItem[]
    planId: string
  } | null
  checkout: {
    proUrl?: string
    scaleUrl?: string
  }
  utmQuerystring?: string
  publicProfile?: {
    handle?: string | null
    suggestedHandle?: string | null
  }
  template?: {
    slug?: string | null
    displayName?: string | null
    url?: string | null
  }
}

export type InsightMetadata = TimeSavedMetadata | BurnRateMetadata | AgentSetupMetadata

export type InsightEvent = {
  insightId: string
  insightType: InsightType
  priority: number
  title: string
  body: string
  metadata: InsightMetadata
  dismissible: boolean
}

export type InsightsResponse = {
  insights: InsightEvent[]
  refreshAfterSeconds: number
}
