/**
 * Insight types for the agent working state.
 * Insights are contextual, helpful information shown inline during processing.
 */

export type InsightType = 'burn_rate' | 'agent_setup'

// Timing constants for insight display
export const INSIGHT_TIMING = {
  showAfterMs: 800, // Delay before first insight appears
  rotationIntervalMs: 10000, // Time between rotations
  fadeInMs: 300, // Fade in duration
  fadeOutMs: 200, // Fade out duration
  minProcessingMs: 3000, // Don't show insights if processing < 3s
} as const

export type BurnRateMetadata = {
  agentName: string
  todayUsage: UsageGaugeMetadata
  monthUsage: UsageGaugeMetadata
  usageUrl?: string
}

export type UsageInsightUpdatePayload = {
  agent_id?: string
  insight_type?: 'burn_rate'
  metadata: BurnRateMetadata
  timestamp?: string
}

export type UsageGaugeMetadata = {
  used: number
  limit: number | null
  percentUsed: number | null
  unlimited: boolean
}

export type AgentSetupPanel = 'always_on' | 'sms' | 'upsell_pro' | 'upsell_scale'

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
  agentName?: string | null
  agentEmail?: string | null
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
}

export type InsightMetadata = BurnRateMetadata | AgentSetupMetadata

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
