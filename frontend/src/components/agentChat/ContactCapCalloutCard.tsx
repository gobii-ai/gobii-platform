import { Users, Zap } from 'lucide-react'

import { LimitCalloutActions, LimitCalloutButton, LimitCalloutCard, useAuthenticatedUpgrade } from './LimitCalloutCard'

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
  const canShowUpgrade = Boolean(showUpgrade)
  const showActions = Boolean(onOpenPacks || canShowUpgrade)
  const handleUpgradeClick = useAuthenticatedUpgrade('contact_cap_callout')

  return (
    <LimitCalloutCard
      title="Contact limit reached"
      subtitle="This agent has hit its contact cap for the current cycle."
      onDismiss={onDismiss}
      dismissLabel="Dismiss contact limit warning"
    >
      {showActions ? (
        <LimitCalloutActions>
          {onOpenPacks ? (
            <LimitCalloutButton onClick={onOpenPacks}>
              <Users size={16} />
              Open add-ons
            </LimitCalloutButton>
          ) : null}
          {canShowUpgrade ? (
            <div className="hard-limit-callout-upsell">
              <span>Need more contacts? Upgrade your plan to expand the contact cap.</span>
              <button type="button" className="banner-upgrade banner-upgrade--text" onClick={handleUpgradeClick}>
                <Zap size={14} strokeWidth={2} />
                <span>Upgrade</span>
              </button>
            </div>
          ) : null}
        </LimitCalloutActions>
      ) : null}
    </LimitCalloutCard>
  )
}
