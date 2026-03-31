import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'

import { ImmersiveApp } from './ImmersiveApp'

const {
  rosterData,
  agentChatPageMock,
} = vi.hoisted(() => ({
  rosterData: {
    context: {
      type: 'personal',
      id: 'user-1',
      name: 'Test User',
    },
    agents: [],
    agentRosterSortMode: 'recent',
    favoriteAgentIds: [],
    insightsPanelExpanded: null,
    requestedAgentStatus: null,
    billingStatus: null,
    llmIntelligence: null,
  },
  agentChatPageMock: vi.fn(() => <div data-testid="agent-chat-page" />),
}))

vi.mock('./AgentChatPage', () => ({
  AgentChatPage: (props: unknown) => {
    agentChatPageMock(props)
    return <div data-testid="agent-chat-page" />
  },
}))

vi.mock('../hooks/useAgentRoster', () => ({
  useAgentRoster: vi.fn(() => ({
    data: rosterData,
    isLoading: false,
  })),
}))

vi.mock('../api/http', () => ({
  jsonFetch: vi.fn(async () => ({
    user_id: '1',
    email: 'user@example.com',
  })),
}))

function renderImmersiveApp() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ImmersiveApp
        maxChatUploadSizeBytes={123}
        pipedreamAppsSettingsUrl="/console/api/pipedream/apps/"
        pipedreamAppSearchUrl="/console/api/pipedream/apps/search/"
      />
    </QueryClientProvider>,
  )
}

describe('ImmersiveApp', () => {
  beforeEach(() => {
    agentChatPageMock.mockClear()
    window.history.pushState({}, '', '/app/agents/agent-123')
  })

  afterEach(() => {
    window.history.pushState({}, '', '/')
  })

  it('passes pipedream app urls through to the live chat page', async () => {
    renderImmersiveApp()

    expect(await screen.findByTestId('agent-chat-page')).toBeInTheDocument()
    expect(agentChatPageMock).toHaveBeenCalledWith(
      expect.objectContaining({
        agentId: 'agent-123',
        pipedreamAppsSettingsUrl: '/console/api/pipedream/apps/',
        pipedreamAppSearchUrl: '/console/api/pipedream/apps/search/',
      }),
    )
  })
})
