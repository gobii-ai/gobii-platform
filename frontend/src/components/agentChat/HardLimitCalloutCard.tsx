import { ExternalLink, Settings, Zap } from 'lucide-react'

import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'
import { appendReturnTo } from '../../util/returnTo'
import { LimitCalloutActions, LimitCalloutButton, LimitCalloutCard } from './LimitCalloutCard'

type HardLimitCalloutCardProps = {
  onOpenSettings: () => void
  onQuickIncrease?: () => void
  quickIncreaseLabel?: string
  quickIncreaseBusy?: boolean
  showUpsell?: boolean
  upgradeUrl?: string | null
}

export function HardLimitCalloutCard({
  onOpenSettings,
  onQuickIncrease,
  quickIncreaseLabel = 'Increase daily limit',
  quickIncreaseBusy = false,
  showUpsell = false,
  upgradeUrl,
}: HardLimitCalloutCardProps) {
  const upgradeHref = upgradeUrl ? appendReturnTo(upgradeUrl) : null

  return (
    <LimitCalloutCard
      title="Daily task limit reached"
      subtitle="Adjust the daily task limit to keep this agent running."
    >
      <LimitCalloutActions>
        {onQuickIncrease ? (
          <LimitCalloutButton
            variant="secondary"
            onClick={onQuickIncrease}
            disabled={quickIncreaseBusy}
          >
            <Zap size={16} />
            {quickIncreaseBusy ? 'Increasing…' : quickIncreaseLabel}
          </LimitCalloutButton>
        ) : null}
        <LimitCalloutButton onClick={onOpenSettings}>
          <Settings size={16} />
          Open settings
        </LimitCalloutButton>
        {showUpsell ? (
          <div className="hard-limit-callout-upsell">
            <span>Running out of credits? Upgrade to allow your agents to do more work for you.</span>
            {upgradeHref ? (
              <a
                href={upgradeHref}
                target="_blank"
                rel="noreferrer"
                onClick={() => {
                  track(AnalyticsEvent.UPGRADE_CHECKOUT_REDIRECTED, {
                    source: 'hard_limit_callout',
                    target: 'upgrade_url',
                  })
                }}
              >
                Upgrade
                <ExternalLink size={12} />
              </a>
            ) : null}
          </div>
        ) : null}
      </LimitCalloutActions>
    </LimitCalloutCard>
  )
}
