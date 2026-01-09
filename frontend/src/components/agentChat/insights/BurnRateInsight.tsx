import { motion } from 'framer-motion'
import { Zap, Activity } from 'lucide-react'
import type { InsightEvent, BurnRateMetadata } from '../../../types/insight'
import { InsightGauge } from './InsightGauge'

type BurnRateInsightProps = {
  insight: InsightEvent
  onDismiss?: (insightId: string) => void
}

export function BurnRateInsight({ insight, onDismiss }: BurnRateInsightProps) {
  const metadata = insight.metadata as BurnRateMetadata

  const progressPercent = Math.min(100, Math.max(0, metadata.percentUsed))

  const getGaugeColors = (): [string, string] => {
    if (progressPercent >= 90) return ['#f87171', '#dc2626']
    if (progressPercent >= 70) return ['#fbbf24', '#d97706']
    return ['#a78bfa', '#7c3aed']
  }

  const getStatusLabel = () => {
    if (progressPercent >= 90) return 'High usage'
    if (progressPercent >= 70) return 'Moderate'
    return 'On track'
  }

  const getStatusClass = () => {
    if (progressPercent >= 90) return 'insight-status--critical'
    if (progressPercent >= 70) return 'insight-status--warning'
    return 'insight-status--normal'
  }

  const getCardClass = () => {
    if (progressPercent >= 90) return 'insight-card-v2--burn-rate-critical'
    if (progressPercent >= 70) return 'insight-card-v2--burn-rate-warning'
    return 'insight-card-v2--burn-rate'
  }

  return (
    <motion.div
      className={`insight-card-v2 ${getCardClass()}`}
      style={{ background: 'transparent', borderRadius: 0 }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35 }}
    >
      {/* Decorative background shapes */}
      <div className="insight-bg-shape insight-bg-shape--1" />
      <div className="insight-bg-shape insight-bg-shape--2" />

      {/* Main gauge */}
      <motion.div
        className="insight-gauge-wrapper"
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.5, delay: 0.1, ease: [0.25, 0.46, 0.45, 0.94] }}
      >
        <InsightGauge
          value={progressPercent}
          max={100}
          size={120}
          gradientColors={getGaugeColors()}
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
            {Math.round(progressPercent)}
          </motion.span>
          <span className="insight-gauge-unit">%</span>
        </div>
      </motion.div>

      {/* Center content */}
      <motion.div
        className="insight-center-content"
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.15 }}
      >
        <span className="insight-main-title">Credit usage</span>
        <span className={`insight-status-badge ${getStatusClass()}`}>{getStatusLabel()}</span>
      </motion.div>

      {/* Right stats - colorful cards */}
      <motion.div
        className="insight-metric-cards"
        initial={{ opacity: 0, x: 10 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <div className="insight-metric-card insight-metric-card--purple">
          <div className="insight-metric-card-icon">
            <Zap size={16} strokeWidth={2} />
          </div>
          <div className="insight-metric-card-content">
            <span className="insight-metric-card-value">{metadata.agentCreditsPerHour.toFixed(1)}</span>
            <span className="insight-metric-card-label">cr/hr</span>
          </div>
        </div>
        <div className="insight-metric-card insight-metric-card--blue">
          <div className="insight-metric-card-icon">
            <Activity size={16} strokeWidth={2} />
          </div>
          <div className="insight-metric-card-content">
            <span className="insight-metric-card-value">{metadata.allAgentsCreditsPerDay.toFixed(0)}</span>
            <span className="insight-metric-card-label">/ {metadata.dailyLimit}</span>
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
