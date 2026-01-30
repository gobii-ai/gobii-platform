import { useCallback } from 'react'
import { AlertTriangle, PlusSquare, X, Zap } from 'lucide-react'
import { useSubscriptionStore } from '../../stores/subscriptionStore'

type TaskCreditsCalloutCardProps = {
  onOpenPacks?: () => void
  showUpgrade?: boolean
  onDismiss?: () => void
  variant?: 'low' | 'out'
}

export function TaskCreditsCalloutCard({
  onOpenPacks,
  showUpgrade = false,
  onDismiss,
  variant = 'low',
}: TaskCreditsCalloutCardProps) {
  const { openUpgradeModal, ensureAuthenticated } = useSubscriptionStore()
  const isOutOfCredits = variant === 'out'
  const handleUpgradeClick = useCallback(async () => {
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      return
    }
    openUpgradeModal('task_credits_callout')
  }, [ensureAuthenticated, openUpgradeModal])

  return (
    <div className={`timeline-event hard-limit-callout${isOutOfCredits ? ' hard-limit-callout--critical' : ''}`}>
      {onDismiss ? (
        <button
          type="button"
          className="hard-limit-callout-dismiss"
          onClick={onDismiss}
          aria-label="Dismiss task credits warning"
        >
          <X size={16} />
        </button>
      ) : null}
      <div className="hard-limit-callout-header">
        <span className="hard-limit-callout-icon" aria-hidden="true">
          <AlertTriangle size={16} />
        </span>
        <div>
          <p className="hard-limit-callout-title">
            {isOutOfCredits ? 'Out of task credits' : 'Task credits running low'}
          </p>
          <p className="hard-limit-callout-subtitle">
            {isOutOfCredits
              ? 'Your account is out of task credits.'
              : 'Your account is almost out of task credits.'}
            {showUpgrade ? (
              <>
                {' Upgrade to allow your agents to do more work for you. '}
                <button type="button" className="banner-upgrade banner-upgrade--text banner-upgrade--inline" onClick={handleUpgradeClick}>
                  <Zap size={14} strokeWidth={2} />
                  <span>Upgrade</span>
                </button>
              </>
            ) : (
              <span> for this billing period.</span>
            )}
          </p>
        </div>
      </div>
      {onOpenPacks ? (
        <div className="hard-limit-callout-actions">
          <button type="button" className="hard-limit-callout-button" onClick={onOpenPacks}>
            <PlusSquare size={16} />
            Open add-ons
          </button>
        </div>
      ) : null}
    </div>
  )
}
