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
  // Evidence (comps + friction data): one predetermined top-tier trial with a
  // quiet cheaper-plan hatch beats a plan chooser at the card moment. The
  // real decision stays switchable during the trial.
  const estimate = useMemo(() => {
    if (outcomeEstimate) {
      return {
        value: outcomeEstimate.scale,
        max: Math.max(outcomeEstimate.scale, outcomeEstimate.startup),
        unit: outcomeEstimate.unit,
        per: outcomeEstimate.per,
        isOutcome: true,
      }
    }
    return {
      value: planTaskCreditsByPlan.scale,
      max: Math.max(planTaskCreditsByPlan.scale, planTaskCreditsByPlan.startup),
      unit: 'tasks included',
      per: 'month',
      isOutcome: false,
    }
  }, [outcomeEstimate, planTaskCreditsByPlan])

  const sweep = estimate.max > 0 ? Math.max(0.12, estimate.value / estimate.max) : 0.5
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

      <div className="trial-gate__dial" data-plan="scale">
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

      <p className="trial-gate__promise">
        {agentReadyName ? `${agentReadyName.split(' ')[0]} starts` : 'Your agent starts'} the moment your
        trial begins — first {estimate.isOutcome ? estimate.unit : 'results'} typically arrive within hours.
      </p>

      <div className="trial-gate__timeline" aria-label="Trial timeline">
        <div className="trial-gate__tl-row">
          <b>Today</b>
          <span>$0 — agent starts, first results by email</span>
        </div>
        {trialDays > 2 ? (
          <div className="trial-gate__tl-row">
            <b>Day {trialDays - 2}</b>
            <span>reminder email before your trial ends</span>
          </div>
        ) : null}
        <div className="trial-gate__tl-row">
          <b>Day {trialDays + 1}</b>
          <span>first charge — ${PLAN_META.scale.price}/mo Scale (everything included)</span>
        </div>
      </div>

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
      {trialDays > 0 ? (
        <p className="trial-gate__under">
          Not charged until day {trialDays + 1} · reminder email first
        </p>
      ) : null}
    </div>
  )
}
