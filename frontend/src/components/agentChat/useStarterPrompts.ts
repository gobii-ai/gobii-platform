import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'
import type { TimelineEvent } from './types'
import {
  STARTER_PROMPT_POOL,
  selectStarterPrompts,
  type StarterPrompt,
} from './StarterPromptSuggestions'

const EMPTY_PROMPTS: StarterPrompt[] = []

type UseStarterPromptsParams = {
  agentId?: string | null
  events: TimelineEvent[]
  initialLoading: boolean
  spawnIntentLoading: boolean
  hasMoreNewer: boolean
  isWorkingNow: boolean
  onSendMessage?: (body: string, attachments?: File[]) => void | Promise<void>
  promptCount?: number
}

type UseStarterPromptsResult = {
  starterPrompts: StarterPrompt[]
  starterPromptSubmitting: boolean
  handleStarterPromptSelect: (prompt: StarterPrompt, position: number) => Promise<void>
}

export function useStarterPrompts({
  agentId,
  events,
  initialLoading,
  spawnIntentLoading,
  hasMoreNewer,
  isWorkingNow,
  onSendMessage,
  promptCount = 3,
}: UseStarterPromptsParams): UseStarterPromptsResult {
  const [starterPromptSubmitting, setStarterPromptSubmitting] = useState(false)
  const starterPromptInFlightRef = useRef(false)

  const userMessageCount = useMemo(
    () =>
      events.reduce(
        (count, event) => (event.kind === 'message' && !event.message.isOutbound ? count + 1 : count),
        0,
      ),
    [events],
  )

  const starterPrompts = useMemo(
    () => selectStarterPrompts(STARTER_PROMPT_POOL, promptCount),
    [agentId, promptCount, userMessageCount],
  )

  const canShowStarterPrompts = Boolean(
    !initialLoading
    && !spawnIntentLoading
    && !hasMoreNewer
    && (!isWorkingNow || userMessageCount === 1)
    && onSendMessage
    && starterPrompts.length > 0,
  )

  useEffect(() => {
    starterPromptInFlightRef.current = false
    setStarterPromptSubmitting(false)
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
    starterPromptSubmitting,
    handleStarterPromptSelect,
  }
}
