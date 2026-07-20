import type { DailyCreditsInfo } from '../../types/dailyCredits'
import type { IntelligenceTierKey, LlmIntelligenceConfig } from '../../types/llmIntelligence'

export type DailyCreditLimitValue = {
  tier: IntelligenceTierKey
  sliderValue: number
  input: string
}

export type DailyCreditLimitMetrics = {
  min: number
  step: number
  limitMax: number
  max: number
  emptyValue: number
}

type DailyCreditLimitConfig = {
  dailyCredits: DailyCreditsInfo
  intelligence?: LlmIntelligenceConfig | null
  standardLimitFallback: number
}

function tierMultiplier(config: DailyCreditLimitConfig, tier: IntelligenceTierKey): number {
  const multiplier = config.intelligence?.options.find((option) => option.key === tier)?.multiplier
  return Number.isFinite(multiplier) && multiplier && multiplier > 0 ? multiplier : 1
}

export function getDailyCreditLimitMetrics(
  config: DailyCreditLimitConfig,
  tier: IntelligenceTierKey,
): DailyCreditLimitMetrics {
  const { dailyCredits } = config
  const min = dailyCredits.sliderMin
  const step = dailyCredits.sliderStep
  const fallbackMax = dailyCredits.sliderMax
  const fallbackLimitMax = dailyCredits.sliderLimitMax ?? fallbackMax
  const fallbackEmptyValue = dailyCredits.sliderEmptyValue ?? fallbackMax
  const hasTierMultipliers = Boolean(config.intelligence?.options.length)
  const configuredStandardLimit = dailyCredits.standardSliderLimit
  const standardLimit = typeof configuredStandardLimit === 'number' && Number.isFinite(configuredStandardLimit)
    ? configuredStandardLimit
    : config.standardLimitFallback

  if (!hasTierMultipliers) {
    return { min, step, limitMax: fallbackLimitMax, max: fallbackMax, emptyValue: fallbackEmptyValue }
  }

  const limitMax = Math.max(min, Math.round(standardLimit * tierMultiplier(config, tier)))
  const max = limitMax + step
  return { min, step, limitMax, max, emptyValue: max }
}

export function setDailyCreditSliderValue(
  value: DailyCreditLimitValue,
  nextSliderValue: number,
  metrics: DailyCreditLimitMetrics,
): DailyCreditLimitValue {
  const sliderValue = Math.min(
    Math.max(Number.isFinite(nextSliderValue) ? nextSliderValue : metrics.emptyValue, metrics.min),
    metrics.max,
  )
  return {
    ...value,
    sliderValue,
    input: sliderValue === metrics.emptyValue ? '' : String(Math.round(sliderValue)),
  }
}

export function setDailyCreditInputValue(
  value: DailyCreditLimitValue,
  input: string,
  metrics: DailyCreditLimitMetrics,
): DailyCreditLimitValue {
  if (!input.trim()) {
    return setDailyCreditSliderValue({ ...value, input }, metrics.emptyValue, metrics)
  }
  const numeric = Number(input)
  if (!Number.isFinite(numeric)) {
    return setDailyCreditSliderValue({ ...value, input }, metrics.emptyValue, metrics)
  }
  return setDailyCreditSliderValue(
    { ...value, input },
    Math.min(Math.max(Math.round(numeric), metrics.min), metrics.limitMax),
    metrics,
  )
}

export function setDailyCreditTier(
  value: DailyCreditLimitValue,
  nextTier: IntelligenceTierKey,
  config: DailyCreditLimitConfig,
): DailyCreditLimitValue {
  if (nextTier === value.tier) return value

  const currentMetrics = getDailyCreditLimitMetrics(config, value.tier)
  const nextMetrics = getDailyCreditLimitMetrics(config, nextTier)
  if (value.sliderValue >= currentMetrics.emptyValue || !value.input.trim()) {
    return { tier: nextTier, sliderValue: nextMetrics.emptyValue, input: '' }
  }

  const scaledValue = Math.round(
    (value.sliderValue * tierMultiplier(config, nextTier)) / tierMultiplier(config, value.tier),
  )
  if (!Number.isFinite(scaledValue) || scaledValue <= 0 || scaledValue > nextMetrics.limitMax) {
    return { tier: nextTier, sliderValue: nextMetrics.emptyValue, input: '' }
  }
  return {
    tier: nextTier,
    sliderValue: Math.max(scaledValue, nextMetrics.min),
    input: String(Math.round(Math.max(scaledValue, nextMetrics.min))),
  }
}

export function getDailyCreditLimitConfig(
  dailyCredits: DailyCreditsInfo,
  intelligence: LlmIntelligenceConfig | null | undefined,
  standardLimitFallback: number,
): DailyCreditLimitConfig {
  return { dailyCredits, intelligence, standardLimitFallback }
}
