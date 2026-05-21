import { motion } from 'framer-motion'
import { CalendarDays, Gauge } from 'lucide-react'
import type { BurnRateMetadata, InsightEvent, UsageGaugeMetadata } from '../../../types/insight'
import { InsightGauge } from './InsightGauge'

type BurnRateInsightProps = {
  insight: InsightEvent
  onDismiss?: (insightId: string) => void
  onOpenUsage?: () => void
  onOpenQuickSettings?: () => void
  usageUrl?: string | null
}

function clampPercent(value: number | null | undefined): number {
  return Math.min(100, Math.max(0, value ?? 0))
}

function formatCredits(value: number): string {
  if (value >= 100) return Math.round(value).toString()
  if (value >= 10) return value.toFixed(1).replace(/\.0$/, '')
  return value.toFixed(2).replace(/\.?0+$/, '')
}

function UsageGauge({
  title,
  usage,
  icon,
  onAdjust,
  onDetails,
  detailsUrl,
}: {
  title: string
  usage: UsageGaugeMetadata
  icon: 'today' | 'month'
  onAdjust?: () => void
  onDetails?: () => void
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

  if (usage.unlimited) {
    return (
      <div className="usage-gauge-card usage-gauge-card--unlimited">
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
        </div>
      </div>
    )
  }

  return (
    <div className="usage-gauge-card">
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
      </div>
    </div>
  )
}

export function BurnRateInsight({
  insight,
  onDismiss,
  onOpenUsage,
  onOpenQuickSettings,
  usageUrl,
}: BurnRateInsightProps) {
  const metadata = insight.metadata as BurnRateMetadata
  const detailsUrl = metadata.usageUrl || usageUrl || '/console/usage/'

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
        <UsageGauge title="Today" usage={metadata.todayUsage} icon="today" onAdjust={onOpenQuickSettings} />
        <UsageGauge
          title="This month"
          usage={metadata.monthUsage}
          icon="month"
          onDetails={onOpenUsage}
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
