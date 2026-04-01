import type { SignupPreviewState } from '../../types/agentRoster'
import type { PlanTier } from '../../stores/subscriptionStore'
import { SubscriptionUpgradePlans } from '../common/SubscriptionUpgradePlans'

type AgentSignupPreviewPanelProps = {
  status: SignupPreviewState
  currentPlan: PlanTier | null
  onUpgrade?: (plan: PlanTier) => void
}

export function AgentSignupPreviewPanel({
  status,
  currentPlan,
  onUpgrade,
}: AgentSignupPreviewPanelProps) {
  const isPaused = status === 'awaiting_signup_completion'
  const title = isPaused ? 'Keep your agent going' : 'Your agent is working'
  const body = isPaused
    ? 'Start a plan to continue working.'
    : 'Start a plan to talk to your agent.'

  return (
    <section className="px-4 pb-4 pt-3 sm:px-6 lg:px-10">
      <div className="overflow-hidden rounded-[1.75rem] border border-sky-200/80 bg-[linear-gradient(135deg,_rgba(239,246,255,0.98),_rgba(224,242,254,0.96)_48%,_rgba(250,245,255,0.96))]">
        <div className="px-5 py-5 sm:px-6">
          <div className="inline-flex rounded-full bg-sky-600 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-white">
            Signup Preview
          </div>
          <h3 className="mt-3 text-xl font-semibold tracking-tight text-slate-900">{title}</h3>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-700">{body}</p>
        </div>
        <div className="px-3 pb-3 sm:px-4 sm:pb-4">
          <div className="rounded-[1.5rem] bg-white/70 px-2 py-2 backdrop-blur-sm">
            <SubscriptionUpgradePlans
              currentPlan={currentPlan}
              onUpgrade={(plan) => onUpgrade?.(plan)}
              variant="inline"
              source="trial_onboarding"
            />
          </div>
        </div>
      </div>
    </section>
  )
}
