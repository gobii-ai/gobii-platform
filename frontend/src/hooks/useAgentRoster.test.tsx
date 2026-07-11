import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren, ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
    const { rerender } = renderHook(
      ({ contextKey }) => useAgentRoster({ contextKey }),
      { initialProps: { contextKey: 'personal:user-1' }, wrapper },
    )

    await waitFor(() => expect(fetchAgentRosterMock).toHaveBeenCalledTimes(1))
    rerender({ contextKey: 'personal:user-1' })
    expect(fetchAgentRosterMock).toHaveBeenCalledTimes(1)

    rerender({ contextKey: 'organization:org-1' })
    await waitFor(() => expect(fetchAgentRosterMock).toHaveBeenCalledTimes(2))
    expect(fetchAgentRosterMock).toHaveBeenNthCalledWith(1)
    expect(fetchAgentRosterMock).toHaveBeenNthCalledWith(2)
  })
})
