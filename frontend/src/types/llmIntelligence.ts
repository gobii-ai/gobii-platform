export type LlmIntelligenceOption = {
  key: string
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
