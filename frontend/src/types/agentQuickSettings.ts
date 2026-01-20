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
      id?: string | null
      name?: string | null
      isFree?: boolean
    } | null
    upgradeUrl?: string | null
  }
}

export type AgentQuickSettingsUpdatePayload = {
  dailyCredits?: DailyCreditsUpdatePayload
}
