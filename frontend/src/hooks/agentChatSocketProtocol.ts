import type { ConsoleContext } from '../api/context'

export type AgentChatSocketSubscription = {
  agentId: string
  mode: 'active' | 'background'
}

export type AgentChatSocketContextOverride = Pick<ConsoleContext, 'type' | 'id'> | null | undefined

export function normalizeAgentChatSocketSubscriptions(
  subscriptions: AgentChatSocketSubscription[],
): AgentChatSocketSubscription[] {
  const seenAgentIds = new Set<string>()
  const normalized: AgentChatSocketSubscription[] = []

  subscriptions.forEach((subscription) => {
    const agentId = typeof subscription.agentId === 'string' ? subscription.agentId.trim() : ''
    if (!agentId || seenAgentIds.has(agentId)) {
      return
    }
    if (subscription.mode !== 'active' && subscription.mode !== 'background') {
      return
    }
    seenAgentIds.add(agentId)
    normalized.push({ agentId, mode: subscription.mode })
  })

  normalized.sort((left, right) => {
    if (left.mode === right.mode) {
      return 0
    }
    return left.mode === 'active' ? -1 : 1
  })

  return normalized
}

export function findActiveAgentChatSocketId(
  subscriptions: AgentChatSocketSubscription[],
): string | null {
  return subscriptions.find((subscription) => subscription.mode === 'active')?.agentId ?? null
}

export function extractAgentChatSocketEnvelopeAgentId(
  payload: Record<string, unknown>,
): string | null {
  if (typeof payload.agent_id === 'string' && payload.agent_id) {
    return payload.agent_id
  }
  const nestedPayload = payload.payload
  if (nestedPayload && typeof nestedPayload === 'object' && !Array.isArray(nestedPayload)) {
    const nestedAgentId = (nestedPayload as Record<string, unknown>).agent_id
    if (typeof nestedAgentId === 'string' && nestedAgentId) {
      return nestedAgentId
    }
  }
  return null
}

export function syncAgentChatSocketSubscriptions({
  currentSubscriptions,
  desiredSubscriptions,
  contextOverride,
  sendSocketMessage,
  handleSendFailure,
}: {
  currentSubscriptions: Map<string, AgentChatSocketSubscription['mode']>
  desiredSubscriptions: AgentChatSocketSubscription[]
  contextOverride: AgentChatSocketContextOverride
  sendSocketMessage: (payload: Record<string, unknown>) => boolean
  handleSendFailure: () => void
}): boolean {
  const desiredSubscriptionsMap = new Map(
    desiredSubscriptions.map((subscription) => [subscription.agentId, subscription.mode]),
  )

  for (const currentAgentId of Array.from(currentSubscriptions.keys())) {
    if (desiredSubscriptionsMap.has(currentAgentId)) {
      continue
    }
    if (!sendSocketMessage({ type: 'unsubscribe', agent_id: currentAgentId })) {
      handleSendFailure()
      return false
    }
    currentSubscriptions.delete(currentAgentId)
  }

  for (const subscription of desiredSubscriptions) {
    if (currentSubscriptions.get(subscription.agentId) === subscription.mode) {
      continue
    }
    const payload: Record<string, unknown> = {
      type: 'subscribe',
      agent_id: subscription.agentId,
      mode: subscription.mode,
    }
    if (contextOverride?.type && contextOverride?.id) {
      payload.context = { type: contextOverride.type, id: contextOverride.id }
    }
    if (!sendSocketMessage(payload)) {
      handleSendFailure()
      return false
    }
    currentSubscriptions.set(subscription.agentId, subscription.mode)
  }

  return true
}
