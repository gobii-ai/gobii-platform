import { memo } from 'react'
import { AlertTriangle, BarChart3, Zap } from 'lucide-react'
import type { CreditForecast } from '../../types/agentChat'
import { AgentChatSectionCard } from './uiPrimitives'

type CreditForecastTimelineCardProps = {
  forecast?: CreditForecast | null
}

type ForecastMetric = {
  label: string
  value: string
}

function formatCredits(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'n/a'
  }
  if (value >= 100) return Math.round(value).toLocaleString()
  if (value >= 10) return value.toFixed(1).replace(/\.0$/, '')
  return value.toFixed(2).replace(/\.?0+$/, '')
}

function formatCreditMetric(value: number): string {
  return `${formatCredits(value)} ${value === 1 ? 'credit' : 'credits'}`
}

function isPositiveForecastValue(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

function buildForecastMetrics(forecast: CreditForecast): ForecastMetric[] {
  const metrics: ForecastMetric[] = []

  if (isPositiveForecastValue(forecast.perRunCredits)) {
    metrics.push({ label: 'Per run', value: formatCreditMetric(forecast.perRunCredits) })
  }
  if (isPositiveForecastValue(forecast.dailyCredits)) {
    metrics.push({ label: 'Daily', value: formatCreditMetric(forecast.dailyCredits) })
  }
  if (isPositiveForecastValue(forecast.monthlyCredits)) {
    metrics.push({ label: 'Monthly', value: formatCreditMetric(forecast.monthlyCredits) })
  }

  return metrics
}

function getForecastTone(forecast: CreditForecast): 'info' | 'warning' | 'critical' {
  if (forecast.warningLevel === 'high') return 'critical'
  if (forecast.warningLevel === 'medium') return 'warning'
  return 'info'
}

export const CreditForecastTimelineCard = memo(function CreditForecastTimelineCard({
  forecast,
}: CreditForecastTimelineCardProps) {
  if (!forecast) {
    return null
  }

  const metrics = buildForecastMetrics(forecast)
  if (metrics.length === 0) {
    return null
  }

  const tone = getForecastTone(forecast)
  const Icon = forecast.warningLevel === 'none' ? BarChart3 : AlertTriangle

  return (
    <AgentChatSectionCard
      className="timeline-event credit-forecast-event"
      tone={tone}
      aria-live={forecast.warningLevel === 'high' ? 'assertive' : 'polite'}
    >
      <div className="credit-forecast-event__icon-wrap" aria-hidden="true">
        <Zap size={18} />
      </div>
      <div className="credit-forecast-event__content">
        <div className="credit-forecast-event__header">
          <span className="credit-forecast-event__eyebrow">
            <Icon size={13} strokeWidth={2.2} aria-hidden="true" />
            Estimated cost
          </span>
        </div>
        <div className="credit-forecast-event__metrics" aria-label="Estimated credit usage">
          {metrics.map((metric) => (
            <div key={metric.label} className="credit-forecast-event__metric">
              <span className="credit-forecast-event__metric-label">{metric.label}</span>
              <span className="credit-forecast-event__metric-value">{metric.value}</span>
            </div>
          ))}
        </div>
      </div>
    </AgentChatSectionCard>
  )
})
