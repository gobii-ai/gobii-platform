import { useMemo } from 'react'
import { useTypewriter } from '../../hooks/useTypewriter'
import { useIsMobile } from '../../hooks/useIsMobile'
import { buildThinkingCluster } from './activityEntryUtils'
import { ToolClusterCard } from './ToolClusterCard'

type StreamingThinkingCardProps = {
  cursor: string
  reasoning: string
  isStreaming: boolean
}

export function StreamingThinkingCard({ cursor, reasoning, isStreaming }: StreamingThinkingCardProps) {
  const isMobile = useIsMobile()
  const { displayedContent } = useTypewriter(reasoning, isStreaming, {
    charsPerFrame: isMobile ? 6 : 1,
    frameIntervalMs: isMobile ? 100 : 18,
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
