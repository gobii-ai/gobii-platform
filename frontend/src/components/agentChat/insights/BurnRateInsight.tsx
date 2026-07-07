import { motion } from 'framer-motion'
import { AlertTriangle, CalendarDays, Gauge } from 'lucide-react'
import type { BurnRateMetadata, ForecastCapacityWarning, InsightEvent, UsageGaugeMetadata } from '../../../types/insight'
import { InsightGauge } from '../../common/InsightGauge'

type BurnRateInsightProps = {
  insight: InsightEvent
  onDismiss?: (insightId: string) => void
  onOpenUsage?: () => void
  onOpenQuickSettings?: () => void
  onOpenTaskPacks?: () => void
  forecastCapacityWarning?: ForecastCapacityWarning | null
  usageUrl?: string | null
}

type CapacitySummary = {
  scope: 'daily' | 'monthly'
  title: string
  body: string
}

function clampPercent(value: number | null | undefined): number {
  return Math.min(100, Math.max(0, value ?? 0))
}

function formatCredits(value: number): string {
  if (value >= 100) return Math.round(value).toString()
  if (value >= 10) return value.toFixed(1).replace(/\.0$/, '')
  return value.toFixed(2).replace(/\.?0+$/, '')
}

function formatCreditCount(value: number): string {
  const label = value === 1 ? 'credit' : 'credits'
  return `${formatCredits(value)} ${label}`
}

function forecastEstimateLabel(warning: ForecastCapacityWarning): string {
  if (warning.estimateType === 'monthly') return 'This month'
  if (warning.estimateType === 'daily') return 'Today'
  return 'Next run'
}

function buildForecastCapacitySummary(warning: ForecastCapacityWarning): CapacitySummary {
  return {
    scope: warning.scope,
    title: warning.scope === 'monthly' ? 'Not enough monthly credits' : 'Daily limit too low',
    body: `${forecastEstimateLabel(warning)} needs ${formatCreditCount(warning.estimatedCredits)}.`,
  }
}

function buildDailyLimitSummary(usage: UsageGaugeMetadata): CapacitySummary | null {
  if (usage.unlimited || usage.limit === null) {
    return null
  }
  const limitReached = usage.used >= usage.limit || (usage.percentUsed ?? 0) >= 100
  if (!limitReached) {
    return null
  }
  return {
    scope: 'daily',
    title: 'Daily limit reached',
    body: 'Increase the limit to let this agent keep running today.',
  }
}

function renderForecastCapacityAction(
  summary: CapacitySummary,
  {
    onOpenUsage,
    onOpenQuickSettings,
    onOpenTaskPacks,
    detailsUrl,
  }: {
    onOpenUsage?: () => void
    onOpenQuickSettings?: () => void
    onOpenTaskPacks?: () => void
    detailsUrl?: string
  },
) {
  if (summary.scope === 'daily') {
    return onOpenQuickSettings
      ? <button type="button" className="usage-forecast-summary__action" onClick={onOpenQuickSettings}>Adjust limit</button>
      : null
  }
  if (onOpenTaskPacks) {
    return <button type="button" className="usage-forecast-summary__action" onClick={onOpenTaskPacks}>Add credits</button>
  }
  if (onOpenUsage) {
    return <button type="button" className="usage-forecast-summary__action" onClick={onOpenUsage}>Details</button>
  }
  return detailsUrl
    ? <a className="usage-forecast-summary__action" href={detailsUrl}>Details</a>
    : null
}

function ForecastCapacitySummary({
  summary,
  onOpenUsage,
  onOpenQuickSettings,
  onOpenTaskPacks,
  detailsUrl,
}: {
  summary: CapacitySummary
  onOpenUsage?: () => void
  onOpenQuickSettings?: () => void
  onOpenTaskPacks?: () => void
  detailsUrl?: string
}) {
  const action = renderForecastCapacityAction(summary, {
    onOpenUsage,
    onOpenQuickSettings,
    onOpenTaskPacks,
    detailsUrl,
  })

  return (
    <div className="usage-forecast-summary" data-scope={summary.scope}>
      <span className="usage-forecast-summary__icon" aria-hidden="true">
        <AlertTriangle size={14} strokeWidth={2.2} />
      </span>
      <div className="usage-forecast-summary__copy">
        <span className="usage-forecast-summary__label">{summary.title}</span>
        <span className="usage-forecast-summary__body">{summary.body}</span>
      </div>
      {action}
    </div>
  )
}

function UsageGauge({
  title,
  usage,
  icon,
  onAdjust,
  onDetails,
  onOpenTaskPacks,
  capacitySummary,
  detailsUrl,
}: {
  title: string
  usage: UsageGaugeMetadata
  icon: 'today' | 'month'
  onAdjust?: () => void
  onDetails?: () => void
  onOpenTaskPacks?: () => void
  capacitySummary?: CapacitySummary | null
  detailsUrl?: string
}) {
  const displayValue = clampPercent(usage.percentUsed)
  const centerValue = Math.round(displayValue).toString()
  const label = usage.unlimited
    ? `${formatCredits(usage.used)} credits used`
    : `${formatCredits(usage.used)} / ${formatCredits(usage.limit ?? 0)} credits`
  const iconNode = icon === 'today'
    ? <Gauge size={13} strokeWidth={2.2} />
    : <CalendarDays size={13} strokeWidth={2.2} />
  const action = onAdjust ? (
    <button type="button" className="usage-gauge-card__action" onClick={onAdjust}>
      Adjust
    </button>
  ) : onDetails ? (
    <button type="button" className="usage-gauge-card__action usage-gauge-card__action--details" onClick={onDetails}>
      Details
    </button>
  ) : detailsUrl ? (
    <a className="usage-gauge-card__action usage-gauge-card__action--details" href={detailsUrl}>
      Details
    </a>
  ) : null

  const className = [
    'usage-gauge-card',
    `usage-gauge-card--${icon}`,
    usage.unlimited ? 'usage-gauge-card--unlimited' : null,
    capacitySummary ? 'usage-gauge-card--forecast-warning' : null,
  ].filter(Boolean).join(' ')
  const forecastNotice = capacitySummary ? (
    <ForecastCapacitySummary
      summary={capacitySummary}
      onOpenUsage={onDetails}
      onOpenQuickSettings={onAdjust}
      onOpenTaskPacks={onOpenTaskPacks}
      detailsUrl={detailsUrl}
    />
  ) : null

  if (capacitySummary) {
    return (
      <div className={className}>
        <div className="usage-gauge-card__copy usage-gauge-card__copy--forecast">
          <span className="usage-gauge-card__icon" aria-hidden="true">
            {iconNode}
          </span>
          <span className="usage-gauge-card__title">{title}</span>
          <span className="usage-gauge-card__label">{label}</span>
          {forecastNotice}
        </div>
      </div>
    )
  }

  if (usage.unlimited) {
    return (
      <div className={className}>
        <div className="usage-gauge-card__unlimited-stat">
          <span className="usage-gauge-card__value">{formatCredits(usage.used)}</span>
          <span className="usage-gauge-card__unit">credits</span>
        </div>
        <div className="usage-gauge-card__copy">
          <span className="usage-gauge-card__icon" aria-hidden="true">
            {iconNode}
          </span>
          <span className="usage-gauge-card__title">{title}</span>
          <span className="usage-gauge-card__label">{label}</span>
          <span className="usage-gauge-card__status">Unlimited</span>
          {action}
          {forecastNotice}
        </div>
      </div>
    )
  }

  return (
    <div className={className}>
      <div className="usage-gauge-card__chart">
        <InsightGauge
          value={displayValue}
          max={100}
          size={84}
          gradientColors={['#AA74CE', '#7C4CA0']}
          thickness={8}
          radius="94%"
          showGlow={false}
          trackColor="rgba(170, 116, 206, 0.14)"
        />
        <div className="insight-gauge-center">
          <span className="usage-gauge-card__value">{centerValue}</span>
          <span className="usage-gauge-card__unit">%</span>
        </div>
      </div>
      <div className="usage-gauge-card__copy">
        <span className="usage-gauge-card__icon" aria-hidden="true">
          {iconNode}
        </span>
        <span className="usage-gauge-card__title">{title}</span>
        <span className="usage-gauge-card__label">{label}</span>
        {action}
        {forecastNotice}
      </div>
    </div>
  )
}

export function BurnRateInsight({
  insight,
  onDismiss,
  onOpenUsage,
  onOpenQuickSettings,
  onOpenTaskPacks,
  forecastCapacityWarning,
  usageUrl,
}: BurnRateInsightProps) {
  const metadata = insight.metadata as BurnRateMetadata
  const detailsUrl = metadata.usageUrl || usageUrl || '/app/usage'
  const todayForecastWarning = forecastCapacityWarning?.scope === 'daily' ? forecastCapacityWarning : null
  const monthForecastWarning = forecastCapacityWarning?.scope === 'monthly' ? forecastCapacityWarning : null
  const todayCapacitySummary = todayForecastWarning
    ? buildForecastCapacitySummary(todayForecastWarning)
    : buildDailyLimitSummary(metadata.todayUsage)
  const monthCapacitySummary = monthForecastWarning
    ? buildForecastCapacitySummary(monthForecastWarning)
    : null

  return (
    <motion.div
      className="insight-card-v2 insight-card-v2--burn-rate usage-insight-card"
      style={{ background: 'transparent', borderRadius: 0 }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35 }}
    >
      <motion.div
        className="usage-insight-card__gauges"
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.08 }}
      >
        <UsageGauge
          title="Today"
          usage={metadata.todayUsage}
          icon="today"
          onAdjust={onOpenQuickSettings}
          capacitySummary={todayCapacitySummary}
        />
        <UsageGauge
          title="This month"
          usage={metadata.monthUsage}
          icon="month"
          onDetails={onOpenUsage}
          onOpenTaskPacks={onOpenTaskPacks}
          capacitySummary={monthCapacitySummary}
          detailsUrl={onOpenUsage ? undefined : detailsUrl}
        />
      </motion.div>

      {onDismiss && insight.dismissible && (
        <button
          type="button"
          className="insight-dismiss-v2"
          onClick={() => onDismiss(insight.insightId)}
          aria-label="Dismiss"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      )}
    </motion.div>
  )
}
