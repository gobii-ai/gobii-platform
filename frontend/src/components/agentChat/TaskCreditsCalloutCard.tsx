import { useCallback } from 'react'
import { AlertTriangle, CreditCard, PlusSquare, X, Zap } from 'lucide-react'
import { useSubscriptionStore } from '../../stores/subscriptionStore'
import { AgentChatSectionCard } from './uiPrimitives'

type TaskCreditsCalloutCardProps = {
  onOpenPacks?: () => void
  showUpgrade?: boolean
  billingIssue?: 'no_org_seats' | null
  onPurchaseSeats?: () => void
  onDismiss?: () => void
  variant?: 'low' | 'out'
}

export function TaskCreditsCalloutCard({
  onOpenPacks,
  showUpgrade = false,
  billingIssue = null,
  onPurchaseSeats,
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
  const isNoOrgSeats = billingIssue === 'no_org_seats'

  return (
    <AgentChatSectionCard
      className="timeline-event hard-limit-callout task-credits-callout"
      tone={isOutOfCredits || isNoOrgSeats ? 'critical' : 'warning'}
      data-billing-issue={isNoOrgSeats ? 'no_org_seats' : undefined}
    >
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
            {isNoOrgSeats ? 'No Seats Purchased' : isOutOfCredits ? 'Out of task credits' : 'Task credits running low'}
          </p>
          <p className="hard-limit-callout-subtitle">
            {isNoOrgSeats
              ? 'You do not have an active team membership. Purchase seats and add team members.'
              : isOutOfCredits
              ? 'Your account is out of task credits.'
              : 'Your account is almost out of task credits.'}
            {!isNoOrgSeats ? (
              showUpgrade ? ' Upgrade to allow your agents to do more work for you.' : ' for this billing period.'
            ) : null}
          </p>
        </div>
      </div>
      {(isNoOrgSeats && onPurchaseSeats) || (!isNoOrgSeats && (onOpenPacks || showUpgrade)) ? (
        <div className="hard-limit-callout-actions task-credits-callout-actions">
          {isNoOrgSeats && onPurchaseSeats ? (
            <button type="button" className="hard-limit-callout-button hard-limit-callout-button--purchase" onClick={onPurchaseSeats}>
              <CreditCard size={14} strokeWidth={2} />
              Purchase Seats
            </button>
          ) : null}
          {onOpenPacks && !isNoOrgSeats ? (
            <button type="button" className="hard-limit-callout-button" onClick={onOpenPacks}>
              <PlusSquare size={16} />
              Open add-ons
            </button>
          ) : null}
          {showUpgrade && !isNoOrgSeats ? (
            <button type="button" className="banner-upgrade banner-upgrade--text" onClick={handleUpgradeClick}>
              <Zap size={14} strokeWidth={2} />
              <span>Upgrade</span>
            </button>
          ) : null}
        </div>
      ) : null}
    </AgentChatSectionCard>
  )
}
