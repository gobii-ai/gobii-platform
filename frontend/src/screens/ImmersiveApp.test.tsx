import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ImmersiveApp } from './ImmersiveApp'
import { createTestAppStore, seedSubscriptionState, StoreProvider } from '../test/storeTestUtils'

const {
  jsonFetchMock,
  useAgentRosterMock,
} = vi.hoisted(() => ({
  jsonFetchMock: vi.fn(),
  useAgentRosterMock: vi.fn(),
}))

vi.mock('../api/http', () => ({
  jsonFetch: jsonFetchMock,
}))

vi.mock('../hooks/useAgentRoster', () => ({
  useAgentRoster: useAgentRosterMock,
}))

vi.mock('./AgentChatPage', () => ({
  AgentChatPage: ({ agentId }: { agentId?: string | null }) => (
    <div data-testid="agent-chat-page">{agentId ?? 'selection'}</div>
  ),
}))

vi.mock('./agentCollaborators/AgentCollaboratorInviteResponsePage', () => ({
  AgentCollaboratorInviteResponsePage: () => null,
}))

vi.mock('./apiKeys/ImmersiveApiKeysPage', () => ({
  ImmersiveApiKeysPage: () => null,
}))

vi.mock('./billing/ImmersiveBillingPage', () => ({
  ImmersiveBillingPage: () => null,
}))

vi.mock('./integrations/ImmersiveMcpServersPage', () => ({
  ImmersiveMcpServersPage: () => null,
}))

vi.mock('./organization/ImmersiveOrganizationPage', () => ({
  ImmersiveOrganizationPage: () => null,
}))

vi.mock('./organization/OrganizationInviteAcceptPage', () => ({
  OrganizationInviteAcceptPage: () => null,
}))

vi.mock('./profile/ImmersiveProfilePage', () => ({
  ImmersiveProfilePage: () => null,
}))

vi.mock('./secrets/ImmersiveSecretsPage', () => ({
  ImmersiveSecretsPage: () => null,
}))

vi.mock('./usage/ImmersiveUsagePage', () => ({
  ImmersiveUsagePage: () => null,
}))

vi.mock('../components/common/SubscriptionUpgradeModal', () => ({
  SubscriptionUpgradeModal: () => null,
}))

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })
}

function renderImmersiveApp(path: string) {
  window.history.pushState({}, '', path)
  const queryClient = createTestQueryClient()
  const store = createTestAppStore({ queryClient })
  seedSubscriptionState(store, {
    currentPlan: 'free',
    isLoading: false,
    isProprietaryMode: true,
  })

  return render(
    <StoreProvider store={store}>
      <QueryClientProvider client={queryClient}>
        <ImmersiveApp />
      </QueryClientProvider>
    </StoreProvider>,
  )
}

describe('ImmersiveApp roster loading', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/')
    jsonFetchMock.mockReset()
    jsonFetchMock.mockResolvedValue({ user_id: '1', email: 'user@example.com' })
    useAgentRosterMock.mockReset()
    useAgentRosterMock.mockReturnValue({
      data: { agents: [] },
      isLoading: false,
    })
  })

  it('loads the top-level roster for the command center', () => {
    renderImmersiveApp('/app')

    expect(useAgentRosterMock).toHaveBeenCalledTimes(1)
    expect(screen.getByText('No agents yet')).toBeInTheDocument()
  })

  it('does not load the top-level roster for concrete agent chat routes', () => {
    renderImmersiveApp('/app/agents/agent-1')

    expect(screen.getByTestId('agent-chat-page')).toHaveTextContent('agent-1')
    expect(useAgentRosterMock).not.toHaveBeenCalled()
  })
})
