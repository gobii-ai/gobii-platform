import type { ForecastCapacityWarning, InsightEvent } from '../../../types/insight'
import { BurnRateInsight } from './BurnRateInsight'
import { AgentSetupInsight } from './AgentSetupInsight'

type InsightEventCardProps = {
  insight: InsightEvent
  onDismiss?: (insightId: string) => void
  onOpenUsage?: () => void
  onOpenQuickSettings?: () => void
  onOpenTaskPacks?: () => void
  forecastCapacityWarning?: ForecastCapacityWarning | null
  usageUrl?: string | null
}

export function InsightEventCard({
  insight,
  onDismiss,
  onOpenUsage,
  onOpenQuickSettings,
  onOpenTaskPacks,
  forecastCapacityWarning,
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
          onOpenTaskPacks={onOpenTaskPacks}
          forecastCapacityWarning={forecastCapacityWarning}
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
