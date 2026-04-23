import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { fetchAgentSuggestions } from '../../api/agentChat'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'
import type { TimelineEvent } from './types'
import {
  type StarterPrompt,
} from './StarterPromptSuggestions'

const EMPTY_PROMPTS: StarterPrompt[] = []
const STARTER_PROMPT_FETCH_DELAY_MS = 350
const STARTER_PROMPT_STALE_TIME_MS = 60_000

type UseStarterPromptsParams = {
  agentId?: string | null
  events: TimelineEvent[]
  initialLoading: boolean
  spawnIntentLoading: boolean
  isWorkingNow: boolean
  onSendMessage?: (body: string, attachments?: File[]) => void | Promise<void>
  promptCount?: number
  hasPendingHumanInput: boolean
}

type UseStarterPromptsResult = {
  starterPrompts: StarterPrompt[]
  starterPromptsLoading: boolean
  starterPromptSubmitting: boolean
  handleStarterPromptSelect: (prompt: StarterPrompt, position: number) => Promise<void>
}

function isStarterPrompt(suggestion: unknown): suggestion is StarterPrompt {
  if (!suggestion || typeof suggestion !== 'object') {
    return false
  }
  const candidate = suggestion as Partial<StarterPrompt>
  return (
    typeof candidate.id === 'string'
    && typeof candidate.text === 'string'
    && (
      candidate.category === 'capabilities'
      || candidate.category === 'deliverables'
      || candidate.category === 'integrations'
      || candidate.category === 'planning'
    )
  )
}

function starterPromptsQueryKey(
  agentId: string | null | undefined,
  promptCount: number,
  latestEventCursor: string | null,
  refreshNonce: number,
) {
  return ['agent-starter-prompts', agentId ?? null, promptCount, latestEventCursor, refreshNonce] as const
}

export function useStarterPrompts({
  agentId,
  events,
  initialLoading,
  spawnIntentLoading,
  isWorkingNow,
  onSendMessage,
  promptCount = 3,
  hasPendingHumanInput,
}: UseStarterPromptsParams): UseStarterPromptsResult {
  const [starterPromptSubmitting, setStarterPromptSubmitting] = useState(false)
  const [starterPromptFetchReadyKey, setStarterPromptFetchReadyKey] = useState<string | null>(null)
  const [idleRefreshNonce, setIdleRefreshNonce] = useState(0)
  const starterPromptInFlightRef = useRef(false)
  const wasWorkingRef = useRef(isWorkingNow)

  const userMessageCount = useMemo(
    () =>
      events.reduce(
        (count, event) => (event.kind === 'message' && !event.message.isOutbound ? count + 1 : count),
        0,
      ),
    [events],
  )
  const hasAgentMessage = useMemo(
    () => events.some((event) => event.kind === 'message' && Boolean(event.message.isOutbound)),
    [events],
  )
  const latestEventCursor = useMemo(() => {
    if (!events.length) {
      return null
    }
    return events[events.length - 1]?.cursor ?? null
  }, [events])

  useEffect(() => {
    if (wasWorkingRef.current && !isWorkingNow) {
      setIdleRefreshNonce((current) => current + 1)
    }
    wasWorkingRef.current = isWorkingNow
  }, [isWorkingNow])

  const canRequestSuggestions = Boolean(
    agentId
    && onSendMessage
    && hasAgentMessage
    && !initialLoading
    && !spawnIntentLoading
    && !isWorkingNow
    && !hasPendingHumanInput
  )

  const starterPromptRequestKey = useMemo(
    () => JSON.stringify([agentId ?? null, promptCount, latestEventCursor, idleRefreshNonce]),
    [agentId, idleRefreshNonce, latestEventCursor, promptCount],
  )
  const starterPromptFetchReady = starterPromptFetchReadyKey === starterPromptRequestKey

  const starterPromptsQuery = useQuery({
    queryKey: starterPromptsQueryKey(agentId, promptCount, latestEventCursor, idleRefreshNonce),
    queryFn: async ({ signal }) => {
      if (!agentId) {
        throw new Error('No agentId')
      }
      const payload = await fetchAgentSuggestions(agentId, {
        promptCount,
        signal,
      })
      return (payload.suggestions || []).filter(isStarterPrompt).slice(0, promptCount)
    },
    enabled: canRequestSuggestions && starterPromptFetchReady,
    staleTime: STARTER_PROMPT_STALE_TIME_MS,
    refetchOnWindowFocus: false,
    retry: false,
  })

  useEffect(() => {
    setStarterPromptFetchReadyKey(null)
    if (!canRequestSuggestions) {
      return
    }

    const timeout = window.setTimeout(() => {
      setStarterPromptFetchReadyKey(starterPromptRequestKey)
    }, STARTER_PROMPT_FETCH_DELAY_MS)
    return () => {
      window.clearTimeout(timeout)
    }
  }, [canRequestSuggestions, starterPromptRequestKey])

  useEffect(() => {
    if (starterPromptsQuery.error) {
      console.debug('Failed to fetch agent suggestions.', starterPromptsQuery.error)
    }
  }, [starterPromptsQuery.error])

  const starterPrompts = useMemo(() => starterPromptsQuery.data ?? EMPTY_PROMPTS, [starterPromptsQuery.data])

  const canShowStarterPrompts = Boolean(
    hasAgentMessage
    && !initialLoading
    && !spawnIntentLoading
    && !isWorkingNow
    && onSendMessage
    && !hasPendingHumanInput
  )
  const showStarterPromptLoading = Boolean(
    canShowStarterPrompts
    && (
      (canRequestSuggestions && !starterPromptFetchReady && !starterPromptsQuery.data)
      || starterPromptsQuery.isFetching
      || (starterPromptFetchReady && !starterPromptsQuery.data && !starterPromptsQuery.isError)
    ),
  )

  useEffect(() => {
    starterPromptInFlightRef.current = false
    setStarterPromptSubmitting(false)
    setStarterPromptFetchReadyKey(null)
    setIdleRefreshNonce(0)
    wasWorkingRef.current = false
  }, [agentId])

  const handleStarterPromptSelect = useCallback(
    async (prompt: StarterPrompt, position: number) => {
      if (!onSendMessage || starterPromptInFlightRef.current) {
        return
      }
      starterPromptInFlightRef.current = true
      setStarterPromptSubmitting(true)
      track(AnalyticsEvent.AGENT_CHAT_STARTER_PROMPT_CLICKED, {
        agent_id: agentId ?? null,
        prompt_id: prompt.id,
        prompt_text: prompt.text,
        prompt_category: prompt.category,
        prompt_position: position + 1,
        user_message_count: userMessageCount,
        is_working: isWorkingNow,
      })
      try {
        await onSendMessage(prompt.text)
      } finally {
        starterPromptInFlightRef.current = false
        setStarterPromptSubmitting(false)
      }
    },
    [agentId, isWorkingNow, onSendMessage, userMessageCount],
  )

  return {
    starterPrompts: canShowStarterPrompts ? starterPrompts : EMPTY_PROMPTS,
    starterPromptsLoading: showStarterPromptLoading,
    starterPromptSubmitting,
    handleStarterPromptSelect,
  }
}
