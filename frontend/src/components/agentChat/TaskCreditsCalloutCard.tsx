import { useCallback } from 'react'
import { AlertTriangle, CreditCard, PlusSquare, X, Zap } from 'lucide-react'
import { ensureAuthenticated, subscriptionActions } from '../../store/subscriptionSlice'
import { useAppDispatch } from '../../store/hooks'
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
  const dispatch = useAppDispatch()
  const isOutOfCredits = variant === 'out'
  const handleUpgradeClick = useCallback(async () => {
    const authenticated = await dispatch(ensureAuthenticated()).unwrap()
    if (!authenticated) {
      return
    }
    dispatch(subscriptionActions.openUpgradeModal({ source: 'task_credits_callout' }))
  }, [dispatch])
  const isNoOrgSeats = billingIssue === 'no_org_seats'
  const showCreditActions = !isNoOrgSeats && Boolean(showUpgrade || onOpenPacks)

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
        <div className="hard-limit-callout-content">
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
              showUpgrade ? (
                ' Upgrade to allow your agents to do more work for you.'
              ) : (
                <span> for this billing period.</span>
              )
            ) : null}
          </p>
          {showCreditActions ? (
            <div className="hard-limit-callout-actions">
              {showUpgrade ? (
                <button
                  type="button"
                  className="hard-limit-callout-button hard-limit-callout-button--upgrade"
                  onClick={handleUpgradeClick}
                >
                  <Zap size={16} strokeWidth={2} />
                  Upgrade
                </button>
              ) : null}
              {onOpenPacks ? (
                <button
                  type="button"
                  className="hard-limit-callout-button hard-limit-callout-button--addons"
                  onClick={onOpenPacks}
                >
                  <PlusSquare size={16} />
                  Open add-ons
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
      {isNoOrgSeats && onPurchaseSeats ? (
        <div className="hard-limit-callout-actions">
          <button type="button" className="hard-limit-callout-button hard-limit-callout-button--purchase" onClick={onPurchaseSeats}>
            <CreditCard size={14} strokeWidth={2} />
            Purchase Seats
          </button>
        </div>
      ) : null}
    </AgentChatSectionCard>
  )
}
