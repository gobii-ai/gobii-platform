import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'

import type { ConsoleContext, ConsoleContextData } from '../api/context'
import { consoleContextQueryKey, useConsoleContextSwitcher } from './useConsoleContextSwitcher'

const {
  createOrganizationMock,
  fetchConsoleContextMock,
  switchConsoleContextMock,
} = vi.hoisted(() => ({
  createOrganizationMock: vi.fn(),
  fetchConsoleContextMock: vi.fn(),
  switchConsoleContextMock: vi.fn(),
}))

vi.mock('../api/context', () => ({
  createOrganization: createOrganizationMock,
  fetchConsoleContext: fetchConsoleContextMock,
  switchConsoleContext: switchConsoleContextMock,
}))

function makeContext(id: string, name = id): ConsoleContext {
  return {
    type: id.startsWith('org') ? 'organization' : 'personal',
    id,
    name,
  }
}

function makeContextData(context: ConsoleContext): ConsoleContextData {
  return {
    context,
    personal: makeContext('user-1', 'Test User'),
    organizations: [
      {
        type: 'organization',
        id: 'org-1',
        name: 'Org One',
        role: 'owner',
      },
    ],
    organizationsEnabled: true,
  }
}

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })
}

function createMemoryStorage(): Storage {
  const values = new Map<string, string>()
  return {
    get length() {
      return values.size
    },
    clear: vi.fn(() => values.clear()),
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    key: vi.fn((index: number) => Array.from(values.keys())[index] ?? null),
    removeItem: vi.fn((key: string) => {
      values.delete(key)
    }),
    setItem: vi.fn((key: string, value: string) => {
      values.set(key, value)
    }),
  }
}

function TestProvider({ children, queryClient }: { children: ReactNode; queryClient: QueryClient }) {
  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  )
}

function ContextProbe({
  forAgentId,
  label = 'probe',
  onSwitched,
}: {
  forAgentId?: string
  label?: string
  onSwitched?: (context: ConsoleContext) => void
}) {
  const context = useConsoleContextSwitcher({
    enabled: true,
    forAgentId,
    onSwitched,
  })
  return (
    <div>
      <span data-testid={`${label}-context-id`}>{context.data?.context.id ?? ''}</span>
      <span data-testid={`${label}-resolved-agent`}>{context.resolvedForAgentId ?? ''}</span>
      <button
        type="button"
        onClick={() => {
          void context.switchContext({
            type: 'organization',
            id: 'org-2',
            name: 'Org Two',
          })
        }}
      >
        Switch
      </button>
    </div>
  )
}

describe('useConsoleContextSwitcher', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: createMemoryStorage(),
    })
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      value: createMemoryStorage(),
    })
    window.localStorage.clear()
    window.sessionStorage.clear()
    createOrganizationMock.mockReset()
    fetchConsoleContextMock.mockReset()
    switchConsoleContextMock.mockReset()
  })

  it('dedupes matching in-flight context requests', async () => {
    const queryClient = createTestQueryClient()
    let resolveFetch: (value: ConsoleContextData) => void = () => undefined
    fetchConsoleContextMock.mockReturnValue(new Promise<ConsoleContextData>((resolve) => {
      resolveFetch = resolve
    }))

    render(
      <TestProvider queryClient={queryClient}>
        <ContextProbe forAgentId="agent-1" label="first" />
        <ContextProbe forAgentId="agent-1" label="second" />
      </TestProvider>,
    )

    await waitFor(() => {
      expect(fetchConsoleContextMock).toHaveBeenCalledTimes(1)
    })

    await act(async () => {
      resolveFetch(makeContextData(makeContext('user-1', 'Test User')))
    })

    expect(await screen.findByTestId('first-resolved-agent')).toHaveTextContent('agent-1')
    expect(screen.getByTestId('second-resolved-agent')).toHaveTextContent('agent-1')
  })

  it('resolves the requested agent id after forAgentId changes', async () => {
    const queryClient = createTestQueryClient()
    fetchConsoleContextMock.mockImplementation(async ({ forAgentId }: { forAgentId?: string } = {}) => (
      makeContextData(makeContext(`ctx-${forAgentId ?? 'default'}`))
    ))

    const { rerender } = render(
      <TestProvider queryClient={queryClient}>
        <ContextProbe forAgentId="agent-1" />
      </TestProvider>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('probe-resolved-agent')).toHaveTextContent('agent-1')
    })

    rerender(
      <TestProvider queryClient={queryClient}>
        <ContextProbe forAgentId="agent-2" />
      </TestProvider>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('probe-resolved-agent')).toHaveTextContent('agent-2')
    })
    expect(screen.getByTestId('probe-context-id')).toHaveTextContent('ctx-agent-2')
  })

  it('switchContext stores the updated context, notifies, and updates query data', async () => {
    const queryClient = createTestQueryClient()
    const onSwitched = vi.fn()
    fetchConsoleContextMock.mockResolvedValue(makeContextData(makeContext('user-1', 'Test User')))
    switchConsoleContextMock.mockResolvedValue({
      type: 'organization',
      id: 'org-2',
      name: 'Org Two Updated',
    })

    render(
      <TestProvider queryClient={queryClient}>
        <ContextProbe forAgentId="agent-1" onSwitched={onSwitched} />
      </TestProvider>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('probe-context-id')).toHaveTextContent('user-1')
    })
    fireEvent.click(screen.getByRole('button', { name: 'Switch' }))

    await waitFor(() => {
      expect(onSwitched).toHaveBeenCalledWith({
        type: 'organization',
        id: 'org-2',
        name: 'Org Two Updated',
      })
    })

    expect(window.sessionStorage.getItem('gobii:console:context-id')).toBe('org-2')
    expect(
      queryClient.getQueryData<ConsoleContextData>(consoleContextQueryKey('agent-1'))?.context,
    ).toEqual({
      type: 'organization',
      id: 'org-2',
      name: 'Org Two Updated',
    })
    expect(screen.getByTestId('probe-context-id')).toHaveTextContent('org-2')
  })
})
