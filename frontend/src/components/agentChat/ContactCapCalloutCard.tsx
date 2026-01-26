import { useCallback } from 'react'
import { AlertTriangle, Users, X, Zap } from 'lucide-react'

import { useSubscriptionStore } from '../../stores/subscriptionStore'

type ContactCapCalloutCardProps = {
  onOpenPacks?: () => void
  showUpgrade?: boolean
  onDismiss?: () => void
}

export function ContactCapCalloutCard({
  onOpenPacks,
  showUpgrade = false,
  onDismiss,
}: ContactCapCalloutCardProps) {
  const { openUpgradeModal } = useSubscriptionStore()
  const canShowUpgrade = Boolean(showUpgrade)
  const showActions = Boolean(onOpenPacks || canShowUpgrade)
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
          aria-label="Dismiss contact limit warning"
        >
          <X size={16} />
        </button>
      ) : null}
      <div className="hard-limit-callout-header">
        <span className="hard-limit-callout-icon" aria-hidden="true">
          <AlertTriangle size={16} />
        </span>
        <div>
          <p className="hard-limit-callout-title">Contact limit reached</p>
          <p className="hard-limit-callout-subtitle">This agent has hit a per-channel contact cap for the current cycle.</p>
        </div>
      </div>
      {showActions ? (
        <div className="hard-limit-callout-actions">
          {onOpenPacks ? (
            <button type="button" className="hard-limit-callout-button" onClick={onOpenPacks}>
              <Users size={16} />
              Open add-ons
            </button>
          ) : null}
          {canShowUpgrade ? (
            <div className="hard-limit-callout-upsell">
              <span>Need more contacts per channel? Upgrade your plan to expand the contact cap.</span>
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
