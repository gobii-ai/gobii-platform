import { AlertTriangle, ExternalLink, Users } from 'lucide-react'

type ContactCapCalloutCardProps = {
  onOpenPacks?: () => void
  showUpgrade?: boolean
  upgradeUrl?: string | null
}

export function ContactCapCalloutCard({ onOpenPacks, showUpgrade = false, upgradeUrl }: ContactCapCalloutCardProps) {
  const canShowUpgrade = Boolean(showUpgrade && upgradeUrl)
  const showActions = Boolean(onOpenPacks || canShowUpgrade)

  return (
    <div className="timeline-event hard-limit-callout">
      <div className="hard-limit-callout-header">
        <span className="hard-limit-callout-icon" aria-hidden="true">
          <AlertTriangle size={16} />
        </span>
        <div>
          <p className="hard-limit-callout-title">Contact limit reached</p>
          <p className="hard-limit-callout-subtitle">This agent has hit its contact cap for the current cycle.</p>
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
              <span>Need more contacts? Upgrade your plan to expand the contact cap.</span>
              <a href={upgradeUrl ?? undefined} target="_blank" rel="noreferrer">
                Upgrade
                <ExternalLink size={12} />
              </a>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
