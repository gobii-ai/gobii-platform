import { useMemo, useState } from 'react'

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
 * The trial gate for the signup funnel: one decision, one dial. Scale is the
 * default; the toggle sweeps the outcome needle. Estimates are template-derived
 * and labeled as estimates. Checkout mechanics stay upstream — this only calls
 * onSelectPlan, which routes to the existing Stripe Checkout.
 */
export function TrialGateDial({
  onSelectPlan,
  trialDays,
  planTaskCreditsByPlan,
  agentReadyName,
  briefTitle,
  outcomeEstimate,
}: TrialGateDialProps) {
  const [selected, setSelected] = useState<'startup' | 'scale'>('scale')

  const estimate = useMemo(() => {
    if (outcomeEstimate) {
      return {
        value: selected === 'scale' ? outcomeEstimate.scale : outcomeEstimate.startup,
        max: Math.max(outcomeEstimate.scale, outcomeEstimate.startup),
        unit: outcomeEstimate.unit,
        per: outcomeEstimate.per,
        isOutcome: true,
      }
    }
    const credits = selected === 'scale' ? planTaskCreditsByPlan.scale : planTaskCreditsByPlan.startup
    return {
      value: credits,
      max: Math.max(planTaskCreditsByPlan.scale, planTaskCreditsByPlan.startup),
      unit: 'tasks included',
      per: 'month',
      isOutcome: false,
    }
  }, [outcomeEstimate, planTaskCreditsByPlan, selected])

  const sweep = estimate.max > 0 ? Math.max(0.12, estimate.value / estimate.max) : 0.5
  const price = PLAN_META[selected].price
  const otherLabel = PLAN_META[selected === 'scale' ? 'startup' : 'scale'].label
  const trialLabel = trialDays > 0 ? `${trialDays}-day free trial` : 'free trial'

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
        Start your <span className="trial-gate__accent">{trialLabel}</span>
      </h2>

      <div className="trial-gate__plans" role="radiogroup" aria-label="Plan">
        {(['startup', 'scale'] as const).map((planId) => (
          <button
            key={planId}
            type="button"
            role="radio"
            aria-checked={selected === planId}
            data-testid={`subscription-plan-${planId}`}
            className={`trial-gate__plan${selected === planId ? ' trial-gate__plan--on' : ''}`}
            onClick={() => setSelected(planId)}
          >
            <span className="trial-gate__plan-name">{PLAN_META[planId].label}</span>
            <span className="trial-gate__plan-price">${PLAN_META[planId].price}/mo</span>
            <span className="trial-gate__plan-hint">
              {planId === 'scale' ? 'Maximum intelligence · best value' : 'Most popular'}
            </span>
          </button>
        ))}
      </div>

      <div className="trial-gate__dial" data-plan={selected}>
        <div className="trial-gate__arc" style={{ ['--sweep' as string]: String(sweep) }} aria-hidden="true" />
        <div className="trial-gate__reading">
          <span className="trial-gate__value">
            {estimate.isOutcome ? '~' : ''}
            {estimate.value.toLocaleString()}
          </span>
          <span className="trial-gate__unit">
            {estimate.unit} / {estimate.per}
          </span>
          {estimate.isOutcome ? <span className="trial-gate__estimate-tag">Estimate</span> : null}
        </div>
      </div>
      {estimate.isOutcome ? (
        <p className="trial-gate__hatch">
          Estimates vary by brief.{' '}
          <a href="/contact" data-analytics-cta-id="trial_gate_talk_to_sales">
            For guaranteed results, talk to sales
          </a>
        </p>
      ) : null}

      <p className="trial-gate__zero">
        <b>$0 today</b> · then ${price}/mo · switch to {otherLabel} anytime
      </p>

      <button
        type="button"
        className="trial-gate__go"
        onClick={() => onSelectPlan(selected)}
        data-testid="trial-gate-start"
      >
        Start free trial
      </button>

      <div className="trial-gate__wallets" aria-label="Payment options">
        <span className="trial-gate__wchip">&#63743; Pay</span>
        <span className="trial-gate__wchip">G Pay</span>
        <span className="trial-gate__wchip">Card</span>
        <span className="trial-gate__wsec">via Stripe Checkout</span>
      </div>
      {trialDays > 0 ? (
        <p className="trial-gate__under">
          Not charged until day {trialDays + 1} · reminder email first
        </p>
      ) : null}
    </div>
  )
}
