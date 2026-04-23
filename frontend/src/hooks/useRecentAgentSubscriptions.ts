import { useEffect, useMemo, useState } from 'react'

import type { ConsoleContext } from '../api/context'
import type { AgentRosterEntry } from '../types/agentRoster'
import type { AgentChatSocketSubscription } from './agentChatSocketProtocol'

const BACKGROUND_AGENT_SUBSCRIPTION_LIMIT = 3

function buildSocketContextKey(
  contextReady: boolean,
  context: ConsoleContext | null,
): string | null {
  if (!contextReady || !context) {
    return null
  }
  return `${context.type}:${context.id}`
}

export function useRecentAgentSubscriptions({
  activeAgentId,
  liveAgentId,
  agentContextReady,
  contextReady,
  context,
  rosterAgents,
}: {
  activeAgentId: string | null
  liveAgentId: string | null
  agentContextReady: boolean
  contextReady: boolean
  context: ConsoleContext | null
  rosterAgents: AgentRosterEntry[]
}): AgentChatSocketSubscription[] {
  const [recentlyViewedAgentIds, setRecentlyViewedAgentIds] = useState<string[]>([])
  const socketContextKey = buildSocketContextKey(contextReady, context)

  useEffect(() => {
    setRecentlyViewedAgentIds([])
  }, [socketContextKey])

  useEffect(() => {
    if (!agentContextReady || !activeAgentId) {
      return
    }
    setRecentlyViewedAgentIds((current) => {
      const next = [activeAgentId, ...current.filter((candidateId) => candidateId !== activeAgentId)]
      return next.slice(0, BACKGROUND_AGENT_SUBSCRIPTION_LIMIT + 1)
    })
  }, [activeAgentId, agentContextReady])

  useEffect(() => {
    const currentRosterAgentIds = new Set(rosterAgents.map((agent) => agent.id))
    setRecentlyViewedAgentIds((current) => {
      const next = current.filter((candidateId) => candidateId === activeAgentId || currentRosterAgentIds.has(candidateId))
      return next.length === current.length ? current : next
    })
  }, [activeAgentId, rosterAgents])

  return useMemo(() => {
    if (!contextReady) {
      return []
    }

    const subscriptions: AgentChatSocketSubscription[] = []
    const rosterAgentIds = new Set(rosterAgents.map((agent) => agent.id))

    if (liveAgentId) {
      subscriptions.push({ agentId: liveAgentId, mode: 'active' })
    }

    for (const agentId of recentlyViewedAgentIds) {
      if (agentId === liveAgentId || !rosterAgentIds.has(agentId)) {
        continue
      }
      subscriptions.push({ agentId, mode: 'background' })
      if (subscriptions.length >= BACKGROUND_AGENT_SUBSCRIPTION_LIMIT + (liveAgentId ? 1 : 0)) {
        break
      }
    }

    return subscriptions
  }, [contextReady, liveAgentId, recentlyViewedAgentIds, rosterAgents])
}
