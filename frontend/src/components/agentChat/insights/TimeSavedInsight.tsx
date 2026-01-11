import { motion } from 'framer-motion'
import { Check, Clock } from 'lucide-react'
import type { InsightEvent, TimeSavedMetadata } from '../../../types/insight'
import { InsightGauge } from './InsightGauge'
import '../../../styles/insights.css'

type TimeSavedInsightProps = {
  insight: InsightEvent
  onDismiss?: (insightId: string) => void
}

export function TimeSavedInsight({ insight, onDismiss }: TimeSavedInsightProps) {
  const metadata = insight.metadata as TimeSavedMetadata

  const periodLabel =
    metadata.comparisonPeriod === 'week'
      ? 'this week'
      : metadata.comparisonPeriod === 'month'
        ? 'this month'
        : 'in total'

  // Calculate a reasonable max for the gauge based on the period
  const maxHours =
    metadata.comparisonPeriod === 'week'
      ? Math.max(20, Math.ceil(metadata.hoursSaved / 10) * 10)
      : metadata.comparisonPeriod === 'month'
        ? Math.max(80, Math.ceil(metadata.hoursSaved / 20) * 20)
        : Math.max(200, Math.ceil(metadata.hoursSaved / 50) * 50)

  const formatHours = (val: number) => {
    if (val >= 100) return `${Math.round(val)}`
    return val.toFixed(1)
  }

  const avgMinPerTask = Math.round((metadata.hoursSaved * 60) / Math.max(1, metadata.tasksCompleted))

  return (
    <motion.div
      className="insight-card-v2 insight-card-v2--time-saved"
      style={{ background: 'transparent', borderRadius: 0 }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35 }}
    >
      {/* Main gauge */}
      <motion.div
        className="insight-gauge-wrapper"
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.5, delay: 0.1, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        <InsightGauge
          value={metadata.hoursSaved}
          max={maxHours}
          size={120}
          gradientColors={['#34d399', '#059669']}
          thickness={14}
          showGlow={true}
        />
        <div className="insight-gauge-center">
          <motion.span
            className="insight-gauge-number"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.4, delay: 0.3 }}
          >
            {formatHours(metadata.hoursSaved)}
          </motion.span>
          <span className="insight-gauge-unit">hours</span>
        </div>
      </motion.div>

      {/* Center content */}
      <motion.div
        className="insight-center-content"
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.15 }}
      >
        <span className="insight-main-title">Time saved</span>
        <span className="insight-main-period">{periodLabel}</span>
      </motion.div>

      {/* Right stats - colorful cards */}
      <motion.div
        className="insight-metric-cards"
        initial={{ opacity: 0, x: 10 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <div className="insight-metric-card insight-metric-card--green">
          <div className="insight-metric-card-icon">
            <Check size={16} strokeWidth={2.5} />
          </div>
          <div className="insight-metric-card-content">
            <span className="insight-metric-card-value">{metadata.tasksCompleted}</span>
            <span className="insight-metric-card-label">tasks</span>
          </div>
        </div>
        <div className="insight-metric-card insight-metric-card--slate">
          <div className="insight-metric-card-icon">
            <Clock size={16} strokeWidth={2} />
          </div>
          <div className="insight-metric-card-content">
            <span className="insight-metric-card-value">~{avgMinPerTask}</span>
            <span className="insight-metric-card-label">min avg</span>
          </div>
        </div>
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
