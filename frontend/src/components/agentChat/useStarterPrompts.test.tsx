import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren, ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { TimelineEvent } from './types'
import { useStarterPrompts } from './useStarterPrompts'

const { fetchAgentSuggestionsMock } = vi.hoisted(() => ({
  fetchAgentSuggestionsMock: vi.fn(),
}))

vi.mock('../../api/agentChat', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/agentChat')>()),
  fetchAgentSuggestions: fetchAgentSuggestionsMock,
}))

function messageEvent(cursor: string, isOutbound: boolean): TimelineEvent {
  return {
    kind: 'message',
    cursor,
    message: {
      id: cursor,
      bodyText: 'Message',
      isOutbound,
    },
  }
}

describe('useStarterPrompts', () => {
  let queryClient: QueryClient
  let wrapper: ({ children }: PropsWithChildren) => ReactElement

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    wrapper = ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    fetchAgentSuggestionsMock.mockReset()
    fetchAgentSuggestionsMock.mockResolvedValue({
      suggestions: [{
        id: 'follow-up-1',
        text: 'Summarize the next steps',
        category: 'deliverables',
      }],
    })
  })

  it('dismisses only the current set and restores suggestions for new timeline context', async () => {
    const initialEvents = [
      messageEvent('message-1', false),
      messageEvent('message-2', true),
    ]
    const { result, rerender } = renderHook(
      ({ events, promptCount }) => useStarterPrompts({
        agentId: 'agent-1',
        events,
        initialLoading: false,
        spawnIntentLoading: false,
        isWorkingNow: false,
        onSendMessage: vi.fn(),
        promptCount,
        hasPendingHumanInput: false,
      }),
      { initialProps: { events: initialEvents, promptCount: 3 }, wrapper },
    )

    await waitFor(() => expect(result.current.starterPrompts).toHaveLength(1))

    act(() => result.current.handleStarterPromptDismiss())
    expect(result.current.starterPrompts).toEqual([])
    expect(result.current.starterPromptsLoading).toBe(false)

    rerender({ events: initialEvents, promptCount: 2 })
    expect(result.current.starterPrompts).toEqual([])
    expect(fetchAgentSuggestionsMock).toHaveBeenCalledTimes(1)

    rerender({
      events: [
        ...initialEvents,
        { kind: 'thinking', cursor: 'thinking-3', reasoning: 'Finishing background work' },
      ],
      promptCount: 2,
    })
    expect(result.current.starterPrompts).toEqual([])
    expect(fetchAgentSuggestionsMock).toHaveBeenCalledTimes(1)

    rerender({ events: [...initialEvents, messageEvent('message-3', true)], promptCount: 2 })

    await waitFor(() => expect(fetchAgentSuggestionsMock).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(result.current.starterPrompts).toHaveLength(1))
  })

  it('does not request suggestions while the preference is disabled', async () => {
    vi.useFakeTimers()
    try {
      const { rerender } = renderHook(
        ({ enabled }) => useStarterPrompts({
          agentId: 'agent-1',
          enabled,
          events: [messageEvent('message-1', true)],
          initialLoading: false,
          spawnIntentLoading: false,
          isWorkingNow: false,
          onSendMessage: vi.fn(),
          hasPendingHumanInput: false,
        }),
        { initialProps: { enabled: false }, wrapper },
      )

      await act(async () => {
        await vi.advanceTimersByTimeAsync(500)
      })
      expect(fetchAgentSuggestionsMock).not.toHaveBeenCalled()

      rerender({ enabled: true })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500)
      })
      expect(fetchAgentSuggestionsMock).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
