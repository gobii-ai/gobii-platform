import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AgentChatBanner } from './AgentChatBanner'
import { chatActions } from '../../store/chatSlice'
import { createTestAppStore, seedSubscriptionState, StoreProvider } from '../../test/storeTestUtils'

vi.mock('../../util/analytics', () => ({
  track: vi.fn(),
}))

describe('AgentChatBanner paused state', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect() {}
    })
  })

  it('shows a textual paused badge and removes contact shortcuts', () => {
    const store = createTestAppStore()
    seedSubscriptionState(store, {
      currentPlan: 'free',
      isLoading: false,
      isProprietaryMode: false,
    })
    store.dispatch(chatActions.agentSelected({ agentId: 'agent-1' }))
    store.dispatch(chatActions.agentIdentityUpdated({
      agentId: 'agent-1',
      agentName: 'Ada',
      agentEmail: 'ada@example.com',
      agentSms: '+15551234567',
      agentIsActive: false,
    }))

    render(
      <StoreProvider store={store}>
        <AgentChatBanner />
      </StoreProvider>,
    )

    expect(screen.getByText('Paused')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Email Ada/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Text Ada/ })).not.toBeInTheDocument()
  })
})
