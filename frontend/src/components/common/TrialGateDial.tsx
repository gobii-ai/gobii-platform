import { useMemo } from 'react'

import './trialGateDial.css'

import type { OutcomeEstimate } from '../../api/agentSpawnIntent'
import type { PlanTaskCreditsByPlan, PlanTier } from '../../store/subscriptionSlice'

type TrialGateDialProps = {
  onSelectPlan: (plan: PlanTier) => void
  trialDays: number
  planTaskCreditsByPlan: PlanTaskCreditsByPlan
  agentReadyName?: string | null
  briefTitle?: string | null
  outcomeEstimate?: OutcomeEstimate | null
}

const PLAN_META: Record<'startup' | 'scale', { label: string; price: number }> = {
  startup: { label: 'Pro', price: 50 },
  scale: { label: 'Scale', price: 250 },
}

/**
 * The trial gate as a "what happens next" receipt: the quiz-derived brief,
 * then a ledger — today's start, the recurring engine, the reminder, the
 * first charge. One Scale CTA with a quiet Pro hatch; evidence says the
 * ledger (not a gauge) carries the conversion. Checkout stays upstream.
 */
export function TrialGateDial({
  onSelectPlan,
  trialDays,
  planTaskCreditsByPlan,
  agentReadyName,
  briefTitle,
  outcomeEstimate,
}: TrialGateDialProps) {
  const firstName = agentReadyName ? agentReadyName.split(' ')[0] : 'Your agent'

  const recurring = useMemo(() => {
    if (outcomeEstimate) {
      const cadence = outcomeEstimate.per === 'day' ? 'Every day' : `Every ${outcomeEstimate.per}`
      return {
        cadence,
        line: `~${outcomeEstimate.scale.toLocaleString()} ${outcomeEstimate.unit}, screened & delivered to your inbox`,
        isEstimate: true,
      }
    }
    return {
      cadence: 'Every month',
      line: `${planTaskCreditsByPlan.scale.toLocaleString()} tasks included — your agent works around the clock`,
      isEstimate: false,
    }
  }, [outcomeEstimate, planTaskCreditsByPlan])

  return (
    <div className="trial-gate" data-testid="subscription-plans-grid">
      {agentReadyName ? (
        <div className="trial-gate__who" data-testid="trial-gate-ready-strip">
          <span className="trial-gate__avatar">{agentReadyName.slice(0, 1)}</span>
          <span className="trial-gate__who-text">
            <b>{agentReadyName} is ready</b>
            <small>{briefTitle ? `${briefTitle} · ` : ''}Brief loaded</small>
          </span>
          <span className="trial-gate__pulse" aria-hidden="true" />
        </div>
      ) : null}

      <h2 className="trial-gate__title">
        Here&rsquo;s <span className="trial-gate__accent">what happens next</span>
      </h2>

      <div className="trial-gate__ledger" aria-label="Trial timeline">
        <div className="trial-gate__lrow">
          <b>Today</b>
          <span>
            <i>$0</i> — {firstName} starts · first {outcomeEstimate ? outcomeEstimate.unit : 'results'} by email
            within hours
          </span>
        </div>
        <div className="trial-gate__lrow trial-gate__lrow--rec">
          <b>{recurring.cadence}</b>
          <span>
            <em>{recurring.line}</em>
          </span>
        </div>
        {trialDays > 2 ? (
          <div className="trial-gate__lrow">
            <b>Day {trialDays - 2}</b>
            <span>reminder email before your trial ends</span>
          </div>
        ) : null}
        <div className="trial-gate__lrow">
          <b>Day {trialDays + 1}</b>
          <span>first charge — ${PLAN_META.scale.price}/mo Scale, everything included</span>
        </div>
      </div>

      {recurring.isEstimate ? (
        <p className="trial-gate__estnote">
          Volumes are calibrated estimates for your brief ·{' '}
          <a href="/contact" data-analytics-cta-id="trial_gate_talk_to_sales">
            for guaranteed results, talk to sales
          </a>
        </p>
      ) : null}

      <button
        type="button"
        className="trial-gate__go"
        onClick={() => onSelectPlan('scale')}
        data-testid="subscription-plan-scale"
      >
        Start free trial
      </button>
      <p className="trial-gate__prohatch">
        Prefer to start on Pro (${PLAN_META.startup.price}/mo)?{' '}
        <button type="button" data-testid="subscription-plan-startup" onClick={() => onSelectPlan('startup')}>
          Start your trial on Pro
        </button>{' '}
        — switch or cancel anytime.
      </p>

      <div className="trial-gate__wallets" aria-label="Payment options">
        <span className="trial-gate__wchip">&#63743; Pay</span>
        <span className="trial-gate__wchip">G Pay</span>
        <span className="trial-gate__wchip">Card</span>
        <span className="trial-gate__wsec">via Stripe Checkout</span>
      </div>
    </div>
  )
}
