import type { InsightEvent, BurnRateMetadata } from '../../../types/insight'
import { AnimatedNumber } from '../../common/AnimatedNumber'

type BurnRateInsightProps = {
  insight: InsightEvent
  onDismiss?: (insightId: string) => void
}

export function BurnRateInsight({ insight, onDismiss }: BurnRateInsightProps) {
  const metadata = insight.metadata as BurnRateMetadata

  // Clamp percent to 0-100 for progress bar
  const progressPercent = Math.min(100, Math.max(0, metadata.percentUsed))

  // Determine progress bar color based on usage
  const progressColor =
    progressPercent >= 90
      ? 'insight-progress--critical'
      : progressPercent >= 70
        ? 'insight-progress--warning'
        : 'insight-progress--normal'

  return (
    <div className="insight-card insight-card--burn-rate">
      <div className="insight-icon">
        <svg
          className="insight-icon-svg insight-icon-svg--burn"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83" />
        </svg>
      </div>

      <div className="insight-content">
        <div className="insight-headline">Credit usage</div>

        <div className="insight-burn-stats">
          <div className="insight-burn-stat">
            <span className="insight-burn-label">{metadata.agentName}</span>
            <span className="insight-burn-value">
              <AnimatedNumber
                value={metadata.agentCreditsPerHour}
                decimals={1}
                className="insight-burn-number"
              />
              <span className="insight-burn-unit"> credits/hr</span>
            </span>
          </div>

          <div className="insight-burn-stat">
            <span className="insight-burn-label">All agents today</span>
            <span className="insight-burn-value">
              <AnimatedNumber
                value={metadata.allAgentsCreditsPerDay}
                decimals={1}
                className="insight-burn-number"
              />
              <span className="insight-burn-unit"> credits</span>
            </span>
          </div>
        </div>

        <div className="insight-progress-container">
          <div className={`insight-progress-bar ${progressColor}`}>
            <div
              className="insight-progress-fill"
              style={{ width: `${progressPercent}%` }}
            />
          </div>
          <span className="insight-progress-label">
            {progressPercent.toFixed(0)}% of daily limit
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
