import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { MessageSearchPanel, type MessageSearchState } from './MessageSearchPanel'
import type { AgentRosterEntry } from '../../types/agentRoster'

function renderPanel({ agents, agentsLoading, query }: {
  agents: AgentRosterEntry[]
  agentsLoading: boolean
  query: string
}) {
  const state: MessageSearchState = { open: true, query, submittedQuery: null }
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MessageSearchPanel
        agents={agents}
        context={null}
        viewerKey="viewer-1"
        agentsLoading={agentsLoading}
        state={state}
        onStateChange={vi.fn()}
      />
    </QueryClientProvider>,
  )
}

const agent = (id: string, name: string): AgentRosterEntry => ({
  id,
  name,
  avatarUrl: null,
  isActive: true,
  processingActive: false,
  lastInteractionAt: null,
  miniDescription: '',
  shortDescription: '',
  listingDescription: '',
  listingDescriptionSource: null,
  displayTags: [],
  detailUrl: `/app/agents/${id}/settings`,
  dailyCreditRemaining: null,
  dailyCreditLow: false,
  last24hCreditBurn: null,
  isOrgOwned: false,
  pendingActionRequestCount: 0,
})

describe('MessageSearchPanel agent results during roster load (bug #509)', () => {
  it('shows a loading indicator, not silence, when a query is typed before agents load', () => {
    renderPanel({ agents: [], agentsLoading: true, query: 'zeta' })
    expect(screen.getByText(/loading agents/i)).toBeInTheDocument()
  })

  it('does not show the loading indicator once agents are loaded', () => {
    renderPanel({ agents: [agent('a1', 'Ada')], agentsLoading: false, query: 'zeta' })
    expect(screen.queryByText(/loading agents/i)).not.toBeInTheDocument()
  })

  it('still lists matches from the partial roster while loading', () => {
    renderPanel({ agents: [agent('a1', 'Zeta Prime')], agentsLoading: true, query: 'zeta' })
    expect(screen.getByText('Zeta Prime')).toBeInTheDocument()
  })
})
