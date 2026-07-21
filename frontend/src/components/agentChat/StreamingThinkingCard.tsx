import { useMemo } from 'react'
import { buildThinkingCluster } from './activityEntryUtils'
import { ToolClusterCard } from './ToolClusterCard'

type StreamingThinkingCardProps = {
  cursor: string
  reasoning: string
  isStreaming: boolean
}

export function StreamingThinkingCard({ cursor, reasoning, isStreaming }: StreamingThinkingCardProps) {
  const cluster = useMemo(
    () => buildThinkingCluster({
      kind: 'thinking',
      cursor,
      reasoning,
    }),
    [cursor, reasoning],
  )

  if (!reasoning.trim()) {
    return null
  }

  return (
    <ToolClusterCard
      cluster={cluster}
      isLatestEvent
      forceActive={isStreaming}
    />
  )
}
