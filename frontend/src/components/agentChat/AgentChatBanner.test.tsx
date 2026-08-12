import { fireEvent, render, screen } from '@testing-library/react'
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

describe('AgentChatBanner description vs plan task (bug #519)', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect() {}
    })
  })

  function setup({ processingActive }: { processingActive: boolean }) {
    const store = createTestAppStore()
    seedSubscriptionState(store, { currentPlan: 'free', isLoading: false, isProprietaryMode: false })
    store.dispatch(chatActions.agentSelected({ agentId: 'agent-1', options: { processingActive } }))
    store.dispatch(chatActions.agentIdentityUpdated({
      agentId: 'agent-1',
      agentName: 'Nadia',
      agentIsActive: true,
      agentMiniDescription: 'Sales research assistant',
    }))
    render(
      <StoreProvider store={store}>
        <AgentChatBanner
          planSnapshot={{
            todoCount: 1,
            doingCount: 1,
            doneCount: 0,
            todoTitles: ['Draft summary'],
            doingTitles: ['Scrape competitor pricing pages'],
            doneTitles: [],
          }}
        />
      </StoreProvider>,
    )
  }

  it('shows "working on <task>" while the agent is actively working', () => {
    setup({ processingActive: true })
    expect(screen.getByText(/working on/i)).toBeInTheDocument()
    expect(screen.getByText(/Scrape competitor pricing pages/)).toBeInTheDocument()
  })

  it('shows the normal description, not the raw plan item, when idle', () => {
    setup({ processingActive: false })
    expect(screen.getByText('Sales research assistant')).toBeInTheDocument()
    expect(screen.queryByText(/Scrape competitor pricing pages/)).not.toBeInTheDocument()
  })
})

describe('AgentChatBanner Debug Mode switch', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      unobserve() {}
      disconnect() {}
    })
  })

  function renderSwitch(developerMode: boolean, onChange = vi.fn()) {
    const store = createTestAppStore()
    seedSubscriptionState(store, { currentPlan: 'free', isLoading: false, isProprietaryMode: false })
    render(
      <StoreProvider store={store}>
        <AgentChatBanner
          developerMode={developerMode}
          showDeveloperMode
          onDeveloperModeChange={onChange}
        />
      </StoreProvider>,
    )
    return onChange
  }

  it('uses accessible switch semantics and toggles the requested state', () => {
    const onChange = renderSwitch(false)
    const toggle = screen.getByRole('switch', { name: 'Debug Mode' })

    expect(toggle).toHaveAttribute('aria-checked', 'false')
    expect(toggle.querySelector('.banner-debug-mode-switch')).toHaveAttribute('data-checked', 'false')
    fireEvent.click(toggle)
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it('shows the same labelled switch in the overflow menu', () => {
    renderSwitch(true)
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))

    const overflowToggle = screen.getByRole('switch', { name: 'Debug Mode' })
    expect(overflowToggle).toHaveClass('banner-overflow-item')
    expect(overflowToggle).toHaveAttribute('aria-checked', 'true')
    expect(overflowToggle.querySelector('.banner-debug-mode-switch')).toHaveAttribute('data-checked', 'true')
  })
})
