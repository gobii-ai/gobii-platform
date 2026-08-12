import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AgentEmptyState, AgentListItem, AgentSearchInput } from './ChatSidebarParts'
import type { AgentRosterEntry } from '../../types/agentRoster'

describe('AgentListItem paused state', () => {
  it('always shows Paused instead of the agent description', () => {
    const agent: AgentRosterEntry = {
      id: 'agent-1',
      name: 'Ada',
      avatarUrl: null,
      isActive: false,
      processingActive: true,
      lastInteractionAt: null,
      miniDescription: 'Research assistant',
      shortDescription: 'Research assistant',
      listingDescription: '',
      listingDescriptionSource: null,
      displayTags: [],
      detailUrl: '/app/agents/agent-1/settings',
      dailyCreditRemaining: null,
      dailyCreditLow: false,
      last24hCreditBurn: null,
      isOrgOwned: false,
      pendingActionRequestCount: 2,
    }

    render(
      <AgentListItem
        agent={agent}
        isActive={false}
        isSwitching={false}
        onSelect={vi.fn()}
        variant="sidebar"
        collapsed={false}
      />,
    )

    expect(screen.getByText('Paused')).toBeInTheDocument()
    expect(screen.queryByText('Research assistant')).not.toBeInTheDocument()
    expect(screen.queryByText(/request/)).not.toBeInTheDocument()
    expect(screen.queryByText('Working')).not.toBeInTheDocument()
  })
})

describe('AgentEmptyState while the roster loads (bug #509 polish)', () => {
  it('renders skeleton rows instead of plain "Loading agents..." text', () => {
    render(
      <AgentEmptyState
        variant="sidebar"
        hasAgents={false}
        loading
        filteredCount={0}
        searchQuery=""
      />,
    )
    expect(screen.getByRole('status', { name: /loading agents/i })).toBeInTheDocument()
    expect(screen.queryByText(/loading agents/i)).not.toBeInTheDocument()
  })
})

describe('AgentSearchInput shortcut hint', () => {
  it('shows the shortcut while empty and gives the space to clear when populated', () => {
    const { rerender } = render(
      <AgentSearchInput
        variant="sidebar"
        value=""
        onChange={vi.fn()}
        onClear={vi.fn()}
        shortcutHint="⌘K"
      />,
    )

    expect(screen.getByText('⌘K')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear search' })).not.toBeInTheDocument()

    rerender(
      <AgentSearchInput
        variant="sidebar"
        value="Ada"
        onChange={vi.fn()}
        onClear={vi.fn()}
        shortcutHint="⌘K"
      />,
    )

    expect(screen.queryByText('⌘K')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear search' })).toBeInTheDocument()
  })
})
