import { motion } from 'framer-motion'
import { ArrowRight, CalendarDays, Gauge } from 'lucide-react'
import type { InsightEvent, BurnRateMetadata, UsageGaugeMetadata } from '../../../types/insight'
import { InsightGauge } from './InsightGauge'

type BurnRateInsightProps = {
  insight: InsightEvent
  onDismiss?: (insightId: string) => void
  onOpenUsage?: () => void
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

function resolveTodayUsage(metadata: BurnRateMetadata): UsageGaugeMetadata {
  return metadata.todayUsage ?? {
    used: metadata.allAgentsCreditsPerDay ?? 0,
    limit: metadata.dailyLimit ?? null,
    percentUsed: metadata.percentUsed ?? null,
    unlimited: metadata.percentUsed == null,
  }
}

function resolveMonthUsage(metadata: BurnRateMetadata): UsageGaugeMetadata {
  return metadata.monthUsage ?? {
    used: metadata.allAgentsCreditsPerDay ?? 0,
    limit: metadata.dailyLimit ?? null,
    percentUsed: metadata.percentUsed ?? null,
    unlimited: false,
  }
}

function UsageGauge({
  title,
  usage,
  icon,
}: {
  title: string
  usage: UsageGaugeMetadata
  icon: 'today' | 'month'
}) {
  const displayValue = usage.unlimited ? usage.used : clampPercent(usage.percentUsed)
  const maxValue = usage.unlimited ? Math.max(10, Math.ceil(usage.used)) : 100
  const centerValue = usage.unlimited ? formatCredits(usage.used) : Math.round(displayValue).toString()
  const centerUnit = usage.unlimited ? 'credits' : '%'
  const label = usage.unlimited
    ? 'Unlimited'
    : `${formatCredits(usage.used)} / ${formatCredits(usage.limit ?? 0)} credits`

  return (
    <div className="usage-gauge-card">
      <div className="usage-gauge-card__chart">
        <InsightGauge
          value={displayValue}
          max={maxValue}
          size={84}
          gradientColors={['#AA74CE', '#7C4CA0']}
          thickness={10}
          showGlow={false}
          trackColor="rgba(170, 116, 206, 0.14)"
        />
        <div className="insight-gauge-center">
          <span className="usage-gauge-card__value">{centerValue}</span>
          <span className="usage-gauge-card__unit">{centerUnit}</span>
        </div>
      </div>
      <div className="usage-gauge-card__copy">
        <span className="usage-gauge-card__icon" aria-hidden="true">
          {icon === 'today' ? <Gauge size={13} strokeWidth={2.2} /> : <CalendarDays size={13} strokeWidth={2.2} />}
        </span>
        <span className="usage-gauge-card__title">{title}</span>
        <span className="usage-gauge-card__label">{label}</span>
      </div>
    </div>
  )
}

export function BurnRateInsight({
  insight,
  onDismiss,
  onOpenUsage,
  usageUrl,
}: BurnRateInsightProps) {
  const metadata = insight.metadata as BurnRateMetadata
  const todayUsage = resolveTodayUsage(metadata)
  const monthUsage = resolveMonthUsage(metadata)
  const detailsUrl = metadata.usageUrl || usageUrl || '/console/usage/'
  const detailAction = (
    <span className="usage-details-card__action">
      Details
      <ArrowRight size={13} strokeWidth={2.4} />
    </span>
  )

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
        <UsageGauge title="Today" usage={todayUsage} icon="today" />
        <UsageGauge title="This month" usage={monthUsage} icon="month" />
      </motion.div>

      {onOpenUsage ? (
        <button type="button" className="usage-details-card" onClick={onOpenUsage}>
          <span className="usage-details-card__title">Usage</span>
          <span className="usage-details-card__body">Open detailed usage and quota trends.</span>
          {detailAction}
        </button>
      ) : (
        <a className="usage-details-card" href={detailsUrl}>
          <span className="usage-details-card__title">Usage</span>
          <span className="usage-details-card__body">Open detailed usage and quota trends.</span>
          {detailAction}
        </a>
      )}

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
