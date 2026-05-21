import type { InsightEvent } from '../../../types/insight'
import { BurnRateInsight } from './BurnRateInsight'
import { AgentSetupInsight } from './AgentSetupInsight'

type InsightEventCardProps = {
  insight: InsightEvent
  onDismiss?: (insightId: string) => void
  onOpenUsage?: () => void
  onOpenQuickSettings?: () => void
  usageUrl?: string | null
}

export function InsightEventCard({
  insight,
  onDismiss,
  onOpenUsage,
  onOpenQuickSettings,
  usageUrl,
}: InsightEventCardProps) {
  switch (insight.insightType) {
    case 'burn_rate':
      return (
        <BurnRateInsight
          insight={insight}
          onDismiss={onDismiss}
          onOpenUsage={onOpenUsage}
          onOpenQuickSettings={onOpenQuickSettings}
          usageUrl={usageUrl}
        />
      )
    case 'agent_setup':
      return <AgentSetupInsight insight={insight} />
    default:
      // Fallback for unknown types - shouldn't happen but TypeScript safety
      return null
  }
}
