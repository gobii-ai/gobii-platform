import { useMemo } from 'react'
import { useTypewriter } from '../../hooks/useTypewriter'
import { buildThinkingCluster } from './activityEntryUtils'
import { ToolClusterCard } from './ToolClusterCard'

type StreamingThinkingCardProps = {
  cursor: string
  reasoning: string
  isStreaming: boolean
}

export function StreamingThinkingCard({ cursor, reasoning, isStreaming }: StreamingThinkingCardProps) {
  const { displayedContent } = useTypewriter(reasoning, isStreaming, {
    charsPerFrame: 1,
    frameIntervalMs: 18,
    waitingThresholdMs: 120,
  })
  const cluster = useMemo(
    () => buildThinkingCluster({
      kind: 'thinking',
      cursor,
      reasoning: displayedContent,
    }),
    [cursor, displayedContent],
  )

  if (!displayedContent.trim()) {
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
