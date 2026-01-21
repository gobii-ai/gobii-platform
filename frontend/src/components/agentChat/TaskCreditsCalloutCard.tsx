import { useCallback } from 'react'
import { AlertTriangle, PlusSquare, X, Zap } from 'lucide-react'
import { useSubscriptionStore } from '../../stores/subscriptionStore'

type TaskCreditsCalloutCardProps = {
  onOpenPacks?: () => void
  showUpgrade?: boolean
  onDismiss?: () => void
}

export function TaskCreditsCalloutCard({
  onOpenPacks,
  showUpgrade = false,
  onDismiss,
}: TaskCreditsCalloutCardProps) {
  const { openUpgradeModal } = useSubscriptionStore()
  const showActions = Boolean(onOpenPacks || showUpgrade)
  const handleUpgradeClick = useCallback(() => {
    openUpgradeModal()
  }, [openUpgradeModal])

  return (
    <div className="timeline-event hard-limit-callout">
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
          <p className="hard-limit-callout-title">Task credits running low</p>
          <p className="hard-limit-callout-subtitle">
            Your account is almost out of task credits{showUpgrade ? "." : " for this billing period."}
          </p>
        </div>
      </div>
      {showActions ? (
        <div className="hard-limit-callout-actions">
          {onOpenPacks ? (
            <button type="button" className="hard-limit-callout-button" onClick={onOpenPacks}>
              <PlusSquare size={16} />
              Open add-ons
            </button>
          ) : null}
          {showUpgrade ? (
            <div className="hard-limit-callout-upsell">
              <span>Upgrade to allow your agents to do more work for you.</span>
              <button type="button" className="banner-upgrade banner-upgrade--text" onClick={handleUpgradeClick}>
                <Zap size={14} strokeWidth={2} />
                <span>Upgrade</span>
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
