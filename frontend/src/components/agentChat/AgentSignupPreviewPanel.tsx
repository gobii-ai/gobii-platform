import type { SignupPreviewState } from '../../types/agentRoster'
import type { PlanTier } from '../../stores/subscriptionStore'
import { useSubscriptionStore } from '../../stores/subscriptionStore'
import { AgentUpgradePlansPanel } from './AgentUpgradePlansPanel'

type AgentSignupPreviewPanelProps = {
  status: SignupPreviewState
  agentId?: string | null
  agentName?: string | null
  onUpgrade?: (plan: PlanTier, source?: string) => void
}

export function AgentSignupPreviewPanel({
  status,
  agentId,
  agentName,
  onUpgrade,
}: AgentSignupPreviewPanelProps) {
  const ctaUnlockAgentCopy = useSubscriptionStore((state) => state.ctaUnlockAgentCopy)
  const isPaused = status === 'awaiting_signup_completion'
  const resolvedAgentName = agentName?.trim() || 'Your agent'
  const title = ctaUnlockAgentCopy
    ? `${resolvedAgentName} is ready.`
    : (isPaused ? 'Keep your agent going' : 'Your agent is working')
  const body = ctaUnlockAgentCopy
    ? 'Unlock your agent now.'
    : (
        isPaused
          ? 'Start a plan to continue working.'
          : 'Start a plan to talk to your agent.'
      )

  return (
    <AgentUpgradePlansPanel
      title={title}
      body={body}
      onUpgrade={onUpgrade}
      source="signup_preview_panel"
      trialCopyVariant={ctaUnlockAgentCopy ? 'unlock_agent' : 'default'}
      signupPreviewAgentId={agentId}
      signupPreviewState={status}
    />
  )
}
