import type { PlanTier } from '../../stores/subscriptionStore'
import type { SignupPreviewState } from '../../types/agentRoster'
import { SubscriptionUpgradePlans } from '../common/SubscriptionUpgradePlans'

type AgentUpgradePlansPanelProps = {
  title: string
  body: string
  currentPlan: PlanTier | null
  onUpgrade?: (plan: PlanTier, source?: string) => void
  source: string
  trialCopyVariant?: 'default' | 'unlock_agent'
  signupPreviewAgentId?: string | null
  signupPreviewState?: SignupPreviewState | null
}

export function AgentUpgradePlansPanel({
  title,
  body,
  currentPlan,
  onUpgrade,
  source,
  trialCopyVariant = 'default',
  signupPreviewAgentId = null,
  signupPreviewState = null,
}: AgentUpgradePlansPanelProps) {
  return (
    <section className="px-1.5 pb-1.5 pt-1.5 sm:px-3 sm:pb-3 sm:pt-2 lg:px-6">
      <div className="overflow-hidden rounded-[1.75rem] border border-sky-200/80 bg-[linear-gradient(135deg,_rgba(239,246,255,0.98),_rgba(224,242,254,0.96)_48%,_rgba(250,245,255,0.96))]">
        <div className="px-3 py-3 sm:px-4 sm:py-4">
          <h3 className="text-base font-semibold tracking-tight text-slate-900 sm:text-lg">{title}</h3>
          <p className="mt-1 max-w-2xl text-[13px] leading-5 text-slate-700 sm:text-sm">{body}</p>
        </div>
        <div className="px-1.5 pb-1.5 sm:px-2.5 sm:pb-2.5">
          <div className="max-h-[min(40dvh,22rem)] overflow-y-auto overscroll-contain rounded-[1.15rem] bg-white/70 px-1 py-1 backdrop-blur-sm [-webkit-overflow-scrolling:touch] [touch-action:pan-y] sm:max-h-none sm:overflow-visible sm:px-1.5 sm:py-1.5">
            <SubscriptionUpgradePlans
              currentPlan={currentPlan}
              onUpgrade={(plan) => onUpgrade?.(plan, source)}
              variant="inline"
              source={source}
              collapseFeaturesByDefault
              trialCopyVariant={trialCopyVariant}
              signupPreviewAgentId={signupPreviewAgentId}
              signupPreviewState={signupPreviewState}
            />
          </div>
        </div>
      </div>
    </section>
  )
}
