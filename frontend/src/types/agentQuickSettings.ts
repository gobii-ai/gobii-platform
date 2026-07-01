import type { DailyCreditsInfo, DailyCreditsStatus, DailyCreditsUpdatePayload } from './dailyCredits'

export type AgentQuickSettings = {
  dailyCredits?: DailyCreditsInfo | null
}

export type AgentQuickSettingsStatus = {
  dailyCredits?: DailyCreditsStatus | null
}

export type AgentQuickSettingsResponse = {
  settings: AgentQuickSettings
  status: AgentQuickSettingsStatus
  meta?: {
    plan?: {
      isFree?: boolean
    } | null
    upgradeUrl?: string | null
  }
}

export type AgentQuickSettingsUpdatePayload = {
  dailyCredits?: DailyCreditsUpdatePayload
}
