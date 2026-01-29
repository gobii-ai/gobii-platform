export type IntelligenceTierKey = 'standard' | 'premium' | 'max' | 'ultra' | 'ultra_max'

export type LlmIntelligenceOption = {
  key: IntelligenceTierKey
  label: string
  description: string
  multiplier: number
}

export type LlmIntelligenceConfig = {
  options: LlmIntelligenceOption[]
  canEdit: boolean
  disabledReason: string | null
  upgradeUrl: string | null
}
