import type { InsightEvent, TimeSavedMetadata } from '../../../types/insight'
import { AnimatedNumber } from '../../common/AnimatedNumber'

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

  return (
    <div className="insight-card insight-card--time-saved">
      <div className="insight-icon">
        <svg
          className="insight-icon-svg insight-icon-svg--time"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      </div>

      <div className="insight-content">
        <div className="insight-headline">You've saved approximately</div>

        <div className="insight-hero-stat">
          <AnimatedNumber
            value={metadata.hoursSaved}
            suffix=" hours"
            decimals={1}
            className="insight-hero-number"
          />
          <span className="insight-period">{periodLabel}</span>
        </div>

        <div className="insight-supporting">
          <AnimatedNumber value={metadata.tasksCompleted} decimals={0} />
          <span> tasks completed</span>
          <span className="insight-methodology" title={metadata.methodology}>
            <svg
              className="insight-info-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="16" x2="12" y2="12" />
              <line x1="12" y1="8" x2="12.01" y2="8" />
            </svg>
          </span>
        </div>
      </div>

      {onDismiss && insight.dismissible && (
        <button
          type="button"
          className="insight-dismiss"
          onClick={() => onDismiss(insight.insightId)}
          aria-label="Dismiss"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      )}
    </div>
  )
}
