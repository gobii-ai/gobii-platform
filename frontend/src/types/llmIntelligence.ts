export type IntelligenceTierKey = 'standard' | 'premium' | 'max' | 'ultra' | 'ultra_max'

export type LlmIntelligenceOption = {
  key: IntelligenceTierKey
  label: string
  description: string
  multiplier: number
  rank?: number | null
}

export type LlmIntelligenceConfig = {
  options: LlmIntelligenceOption[]
  canEdit: boolean
  disabledReason: string | null
  upgradeUrl: string | null
  maxAllowedTier?: IntelligenceTierKey | null
  maxAllowedTierRank?: number | null
}
