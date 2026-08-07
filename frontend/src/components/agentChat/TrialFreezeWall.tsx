import type { PlanTier } from '../../store/subscriptionSlice'
import { selectSubscriptionState } from '../../store/subscriptionSlice'
import { useAppSelector } from '../../store/hooks'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'

import './trialFreezeWall.css'

/**
 * The signup freeze wall: the agent has worked once and is paused until a
 * plan starts. One primary CTA into the Scale trial with a quiet Pro escape
 * hatch — evidence says decisions at the card moment cost trials, so the
 * real plan choice stays switchable during the trial. Checkout mechanics are
 * upstream via onSelectPlan.
 */
export function TrialFreezeWall({
  agentName,
  onSelectPlan,
  source = 'signup_preview_panel',
}: {
  agentName?: string | null
  onSelectPlan: (plan: PlanTier) => void
  source?: string
}) {
  const { currentPlan, trialDaysByPlan } = useAppSelector(selectSubscriptionState)
  const trialDays = Math.max(trialDaysByPlan.startup, trialDaysByPlan.scale)
  const name = agentName?.trim() || 'Your agent'

  const choose = (plan: PlanTier) => {
    track(AnalyticsEvent.UPGRADE_PLAN_SELECTED, {
      currentPlan,
      selectedPlan: plan,
      source,
    })
    onSelectPlan(plan)
  }

  return (
    <div className="freeze-wall" data-testid="trial-freeze-wall">
      <span className="freeze-wall__aurora" aria-hidden="true" />
      <span className="freeze-wall__aurora-glow" aria-hidden="true" />
      <div className="freeze-wall__who">
        <span className="freeze-wall__avatar">{name.slice(0, 1)}</span>
        <span className="freeze-wall__who-text">
          <b>{name} is mid-job</b>
          <small>paused until your trial starts · your messages will queue</small>
        </span>
        <span className="freeze-wall__pulse" aria-hidden="true" />
      </div>
      <h2 className="freeze-wall__title">
        Unlock everything found — <em>keep {name.split(' ')[0]} working</em>
      </h2>
      <p className="freeze-wall__zero">
        <b>$0 today</b>
        {trialDays > 0 ? ` · ${trialDays}-day trial` : ''} · everything in Scale included · then $250/mo
      </p>
      <button
        type="button"
        className="freeze-wall__go"
        data-testid="subscription-plan-scale"
        onClick={() => choose('scale')}
      >
        <span>Start free trial &amp; unlock</span>
      </button>
      <p className="freeze-wall__hatch">
        Prefer to start on Pro ($50/mo)?{' '}
        <button type="button" data-testid="subscription-plan-startup" onClick={() => choose('startup')}>
          Start your trial on Pro
        </button>{' '}
        — switch or cancel anytime.
      </p>
      <div className="freeze-wall__wallets" aria-label="Payment options">
        <span className="freeze-wall__wchip">&#63743; Pay</span>
        <span className="freeze-wall__wchip">G Pay</span>
        <span className="freeze-wall__wchip">Card</span>
        <span className="freeze-wall__wsec">via Stripe Checkout</span>
      </div>
      {trialDays > 0 ? (
        <p className="freeze-wall__under">
          Not charged until day {trialDays + 1} · reminder email first · {name.split(' ')[0]} keeps working through
          your trial
        </p>
      ) : null}
    </div>
  )
}
