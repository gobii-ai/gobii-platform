import { AlertTriangle, ExternalLink, Settings } from 'lucide-react'

type HardLimitCalloutCardProps = {
  onOpenSettings: () => void
  showUpsell?: boolean
  upgradeUrl?: string | null
}

export function HardLimitCalloutCard({
  onOpenSettings,
  showUpsell = false,
  upgradeUrl,
}: HardLimitCalloutCardProps) {
  return (
    <div className="timeline-event hard-limit-callout">
      <div className="hard-limit-callout-header">
        <span className="hard-limit-callout-icon" aria-hidden="true">
          <AlertTriangle size={16} />
        </span>
        <div>
          <p className="hard-limit-callout-title">Daily task limit reached</p>
          <p className="hard-limit-callout-subtitle">Adjust the daily task limit to keep this agent running.</p>
        </div>
      </div>
      <div className="hard-limit-callout-actions">
        <button type="button" className="hard-limit-callout-button" onClick={onOpenSettings}>
          <Settings size={16} />
          Open settings
        </button>
        {showUpsell ? (
          <div className="hard-limit-callout-upsell">
            <span>Running out of credits? Upgrade to allow your agents to do more work for you.</span>
            {upgradeUrl ? (
              <a href={upgradeUrl} target="_blank" rel="noreferrer">
                Upgrade
                <ExternalLink size={12} />
              </a>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  )
}
