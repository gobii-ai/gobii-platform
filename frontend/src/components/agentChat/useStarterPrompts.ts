import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchAgentSuggestions } from '../../api/agentChat'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'
import type { TimelineEvent } from './types'
import {
  type StarterPrompt,
} from './StarterPromptSuggestions'

const EMPTY_PROMPTS: StarterPrompt[] = []

type UseStarterPromptsParams = {
  agentId?: string | null
  events: TimelineEvent[]
  initialLoading: boolean
  spawnIntentLoading: boolean
  isWorkingNow: boolean
  onSendMessage?: (body: string, attachments?: File[]) => void | Promise<void>
  promptCount?: number
}

type UseStarterPromptsResult = {
  starterPrompts: StarterPrompt[]
  starterPromptsLoading: boolean
  starterPromptSubmitting: boolean
  handleStarterPromptSelect: (prompt: StarterPrompt, position: number) => Promise<void>
}

export function useStarterPrompts({
  agentId,
  events,
  initialLoading,
  spawnIntentLoading,
  isWorkingNow,
  onSendMessage,
  promptCount = 3,
}: UseStarterPromptsParams): UseStarterPromptsResult {
  const [starterPromptSubmitting, setStarterPromptSubmitting] = useState(false)
  const [backendPrompts, setBackendPrompts] = useState<StarterPrompt[] | null>(null)
  const [starterPromptsLoading, setStarterPromptsLoading] = useState(false)
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
    && !isWorkingNow,
  )

  useEffect(() => {
    if (!canRequestSuggestions || !agentId) {
      setStarterPromptsLoading(false)
      return
    }

    const controller = new AbortController()
    const run = async () => {
      setStarterPromptsLoading(true)
      setBackendPrompts(null)
      try {
        const payload = await fetchAgentSuggestions(agentId, {
          promptCount,
          signal: controller.signal,
        })
        if (controller.signal.aborted) {
          return
        }
        const prompts = (payload.suggestions || []).filter((suggestion): suggestion is StarterPrompt => (
          typeof suggestion?.id === 'string'
          && typeof suggestion?.text === 'string'
          && (
            suggestion?.category === 'capabilities'
            || suggestion?.category === 'deliverables'
            || suggestion?.category === 'integrations'
            || suggestion?.category === 'planning'
          )
        ))
        setBackendPrompts(prompts.slice(0, promptCount))
      } catch (error) {
        if (controller.signal.aborted) {
          return
        }
        console.debug('Failed to fetch agent suggestions.', error)
        setBackendPrompts([])
      } finally {
        if (!controller.signal.aborted) {
          setStarterPromptsLoading(false)
        }
      }
    }

    void run()
    return () => controller.abort()
  }, [agentId, canRequestSuggestions, idleRefreshNonce, promptCount])

  const starterPrompts = useMemo(() => backendPrompts ?? EMPTY_PROMPTS, [backendPrompts])

  const canShowStarterPrompts = Boolean(
    hasAgentMessage
    && !initialLoading
    && !spawnIntentLoading
    && !isWorkingNow
    && onSendMessage
  )
  const showStarterPromptLoading = Boolean(
    canShowStarterPrompts
    && (starterPromptsLoading || backendPrompts === null),
  )

  useEffect(() => {
    starterPromptInFlightRef.current = false
    setStarterPromptSubmitting(false)
    setStarterPromptsLoading(false)
    setBackendPrompts(null)
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
