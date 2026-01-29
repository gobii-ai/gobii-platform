export type DailyCreditsInfo = {
  limit: number | null
  hardLimit: number | null
  usage: number
  remaining: number | null
  softRemaining: number | null
  unlimited: boolean
  percentUsed: number | null
  softPercentUsed: number | null
  nextResetIso: string | null
  nextResetLabel: string | null
  low: boolean
  sliderMin: number
  sliderMax: number
  sliderLimitMax: number
  sliderStep: number
  sliderValue: number
  sliderEmptyValue: number
  standardSliderLimit: number
}

export type DailyCreditsStatus = {
  softTargetExceeded: boolean
  hardLimitReached: boolean
  hardLimitBlocked?: boolean
}

export type DailyCreditsUpdatePayload = {
  daily_credit_limit: number | null
}
