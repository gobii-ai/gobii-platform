import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AgentListItem } from './ChatSidebarParts'
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
