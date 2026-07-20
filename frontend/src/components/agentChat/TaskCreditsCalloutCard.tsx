import { CreditCard, PlusSquare, Zap } from 'lucide-react'
import { LimitCalloutActions, LimitCalloutButton, LimitCalloutCard, useAuthenticatedUpgrade } from './LimitCalloutCard'

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
  const isOutOfCredits = variant === 'out'
  const handleUpgradeClick = useAuthenticatedUpgrade('task_credits_callout')
  const isNoOrgSeats = billingIssue === 'no_org_seats'
  const showCreditActions = !isNoOrgSeats && Boolean(showUpgrade || onOpenPacks)

  return (
    <LimitCalloutCard
      className="task-credits-callout"
      tone={isOutOfCredits || isNoOrgSeats ? 'critical' : 'warning'}
      billingIssue={isNoOrgSeats ? 'no_org_seats' : undefined}
      onDismiss={onDismiss}
      dismissLabel="Dismiss task credits warning"
      title={isNoOrgSeats ? 'No Seats Purchased' : isOutOfCredits ? 'Out of task credits' : 'Task credits running low'}
      subtitle={(
        <>
          {isNoOrgSeats
            ? 'You do not have an active team membership. Purchase seats and add team members.'
            : isOutOfCredits
              ? 'Your account is out of task credits.'
              : 'Your account is almost out of task credits.'}
          {!isNoOrgSeats ? (showUpgrade ? ' Upgrade to allow your agents to do more work for you.' : <span> for this billing period.</span>) : null}
        </>
      )}
      contentActions={showCreditActions ? (
        <LimitCalloutActions>
          {showUpgrade ? (
            <LimitCalloutButton variant="upgrade" onClick={handleUpgradeClick}>
              <Zap size={16} strokeWidth={2} />
              Upgrade
            </LimitCalloutButton>
          ) : null}
          {onOpenPacks ? (
            <LimitCalloutButton variant="addons" onClick={onOpenPacks}>
              <PlusSquare size={16} />
              Open add-ons
            </LimitCalloutButton>
          ) : null}
        </LimitCalloutActions>
      ) : null}
    >
      {isNoOrgSeats && onPurchaseSeats ? (
        <LimitCalloutActions>
          <LimitCalloutButton variant="purchase" onClick={onPurchaseSeats}>
            <CreditCard size={14} strokeWidth={2} />
            Purchase Seats
          </LimitCalloutButton>
        </LimitCalloutActions>
      ) : null}
    </LimitCalloutCard>
  )
}
