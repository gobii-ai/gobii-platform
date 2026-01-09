import type { InsightEvent } from '../../../types/insight'
import { TimeSavedInsight } from './TimeSavedInsight'
import { BurnRateInsight } from './BurnRateInsight'

type InsightEventCardProps = {
  insight: InsightEvent
  onDismiss?: (insightId: string) => void
}

export function InsightEventCard({ insight, onDismiss }: InsightEventCardProps) {
  switch (insight.insightType) {
    case 'time_saved':
      return <TimeSavedInsight insight={insight} onDismiss={onDismiss} />
    case 'burn_rate':
      return <BurnRateInsight insight={insight} onDismiss={onDismiss} />
    default:
      // Fallback for unknown types - shouldn't happen but TypeScript safety
      return null
  }
}
