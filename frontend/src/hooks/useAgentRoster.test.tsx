import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren, ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ConsoleContext } from '../api/context'
import { useAgentRoster } from './useAgentRoster'

const { fetchAgentRosterMock } = vi.hoisted(() => ({
  fetchAgentRosterMock: vi.fn(),
}))

vi.mock('../api/agents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/agents')>()),
  fetchAgentRoster: fetchAgentRosterMock,
}))

describe('useAgentRoster', () => {
  let queryClient: QueryClient
  let wrapper: ({ children }: PropsWithChildren) => ReactElement

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    wrapper = ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    fetchAgentRosterMock.mockReset()
    fetchAgentRosterMock.mockResolvedValue({ agents: [] })
  })

  it('reuses the roster within a context and fetches once when the context changes', async () => {
    const personalContext: ConsoleContext = { type: 'personal', id: 'user-1', name: 'User' }
    const organizationContext: ConsoleContext = { type: 'organization', id: 'org-1', name: 'Org' }
    const { rerender } = renderHook(
      ({ context }) => useAgentRoster({ context, contextKey: `${context.type}:${context.id}` }),
      { initialProps: { context: personalContext }, wrapper },
    )

    await waitFor(() => expect(fetchAgentRosterMock).toHaveBeenCalledTimes(1))
    rerender({ context: personalContext })
    expect(fetchAgentRosterMock).toHaveBeenCalledTimes(1)

    rerender({ context: organizationContext })
    await waitFor(() => expect(fetchAgentRosterMock).toHaveBeenCalledTimes(2))
    expect(fetchAgentRosterMock).toHaveBeenNthCalledWith(1, {
      context: { type: 'personal', id: 'user-1', name: 'User' },
    })
    expect(fetchAgentRosterMock).toHaveBeenNthCalledWith(2, {
      context: { type: 'organization', id: 'org-1', name: 'Org' },
    })
  })
})
