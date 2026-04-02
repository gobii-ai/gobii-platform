import type { InsightEvent } from '../../../types/insight'
import { TimeSavedInsight } from './TimeSavedInsight'
import { BurnRateInsight } from './BurnRateInsight'
import { AgentSetupInsight } from './AgentSetupInsight'

type InsightEventCardProps = {
  insight: InsightEvent
  onDismiss?: (insightId: string) => void
  onCollaborate?: () => void
  collaborateDisabled?: boolean
  collaborateDisabledReason?: string | null
  onBlockedCollaborate?: (location: 'insight_card') => void
}

export function InsightEventCard({
  insight,
  onDismiss,
  onCollaborate,
  collaborateDisabled = false,
  collaborateDisabledReason = null,
  onBlockedCollaborate,
}: InsightEventCardProps) {
  switch (insight.insightType) {
    case 'time_saved':
      return <TimeSavedInsight insight={insight} onDismiss={onDismiss} />
    case 'burn_rate':
      return <BurnRateInsight insight={insight} onDismiss={onDismiss} />
    case 'agent_setup':
      return (
        <AgentSetupInsight
          insight={insight}
          onCollaborate={onCollaborate}
          collaborateDisabled={collaborateDisabled}
          collaborateDisabledReason={collaborateDisabledReason}
          onBlockedCollaborate={onBlockedCollaborate}
        />
      )
    default:
      // Fallback for unknown types - shouldn't happen but TypeScript safety
      return null
  }
}
