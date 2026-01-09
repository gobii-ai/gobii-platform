/**
 * Insight types for the agent working state.
 * Insights are contextual, helpful information shown inline during processing.
 */

export type InsightType = 'time_saved' | 'burn_rate'

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

export type InsightMetadata = TimeSavedMetadata | BurnRateMetadata

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
