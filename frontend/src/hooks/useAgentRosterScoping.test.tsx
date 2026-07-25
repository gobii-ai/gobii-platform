/**
 * Regression coverage for #363: the agent roster was fetched and then discarded.
 *
 * A roster scoped to a specific agent (`for_agent`) comes back carrying that agent's own console
 * context, which can differ from the context the cache key describes. `forAgentId` was missing from
 * the query key, so the scoped response was filed under the unscoped entry and served back for a
 * key it did not belong to. The caller compared the two contexts, called it a mismatch, and emptied
 * the list — a sidebar showing one agent when the server had returned a hundred.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren, ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConsoleContext } from '../api/context'
import { useAgentRoster } from './useAgentRoster'

const { fetchAgentRosterMock } = vi.hoisted(() => ({ fetchAgentRosterMock: vi.fn() }))

vi.mock('../api/agents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/agents')>()),
  fetchAgentRoster: fetchAgentRosterMock,
}))

const PERSONAL: ConsoleContext = { type: 'personal', id: 'user-1', name: 'User' }
const CONTEXT_KEY = 'personal:user-1:normal'

describe('useAgentRoster scoping by agent', () => {
  let queryClient: QueryClient
  let wrapper: ({ children }: PropsWithChildren) => ReactElement

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    wrapper = ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    fetchAgentRosterMock.mockReset()
    fetchAgentRosterMock.mockResolvedValue({ agents: [] })
  })

  it('refetches when the route agent changes within one context', async () => {
    const { rerender } = renderHook(
      ({ forAgentId }) => useAgentRoster({ context: PERSONAL, contextKey: CONTEXT_KEY, forAgentId }),
      { initialProps: { forAgentId: 'agent-a' }, wrapper },
    )

    await waitFor(() => expect(fetchAgentRosterMock).toHaveBeenCalledTimes(1))

    // A different agent is a different request: it resolves a different console context server side.
    rerender({ forAgentId: 'agent-b' })

    await waitFor(() => expect(fetchAgentRosterMock).toHaveBeenCalledTimes(2))
    expect(fetchAgentRosterMock).toHaveBeenNthCalledWith(2, expect.objectContaining({ forAgentId: 'agent-b' }))
  })

  it('does not serve an agent-scoped roster to an unscoped caller', async () => {
    const scoped = { agents: [{ id: 'a' }, { id: 'b' }], context: { type: 'organization', id: 'org-1' } }
    fetchAgentRosterMock.mockResolvedValueOnce(scoped)

    const { rerender } = renderHook(
      ({ forAgentId }) => useAgentRoster({ context: PERSONAL, contextKey: CONTEXT_KEY, forAgentId }),
      { initialProps: { forAgentId: 'agent-a' as string | undefined }, wrapper },
    )
    await waitFor(() => expect(fetchAgentRosterMock).toHaveBeenCalledTimes(1))

    fetchAgentRosterMock.mockResolvedValue({ agents: [], context: { type: 'personal', id: 'user-1' } })
    rerender({ forAgentId: undefined })

    // The unscoped read must issue its own request rather than inherit the organization-scoped one.
    await waitFor(() => expect(fetchAgentRosterMock).toHaveBeenCalledTimes(2))
    expect(fetchAgentRosterMock).toHaveBeenNthCalledWith(2, expect.objectContaining({ forAgentId: undefined }))
  })

  it('still reuses the cache when neither context nor agent changed', async () => {
    const { rerender } = renderHook(
      ({ forAgentId }) => useAgentRoster({ context: PERSONAL, contextKey: CONTEXT_KEY, forAgentId }),
      { initialProps: { forAgentId: 'agent-a' }, wrapper },
    )

    await waitFor(() => expect(fetchAgentRosterMock).toHaveBeenCalledTimes(1))
    rerender({ forAgentId: 'agent-a' })

    expect(fetchAgentRosterMock).toHaveBeenCalledTimes(1)
  })

  it('remains reachable by the bare prefix callers invalidate with', async () => {
    renderHook(() => useAgentRoster({ context: PERSONAL, contextKey: CONTEXT_KEY, forAgentId: 'agent-a' }), { wrapper })

    await waitFor(() => expect(fetchAgentRosterMock).toHaveBeenCalledTimes(1))

    expect(queryClient.getQueryCache().findAll({ queryKey: ['agent-roster'] })).toHaveLength(1)
  })
})
