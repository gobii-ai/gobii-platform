/**
 * Regression coverage for #344: opening an agent URL renders that one agent immediately, while the
 * rest of the roster is still in flight. The header published that partial length as a settled
 * count -- "All agents 1" -- with nothing indicating more was coming.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AgentListSectionHeader } from './ChatSidebarParts'

describe('roster section header while the roster is loading', () => {
  it('withholds the count instead of asserting a number that is about to change', () => {
    const { container } = render(
      <AgentListSectionHeader variant="sidebar" label="All agents" count={1} loading />,
    )

    expect(screen.getByText('All agents')).toBeInTheDocument()
    expect(screen.queryByText('1')).not.toBeInTheDocument()
    expect(container.querySelector('.agent-roster-count-pending')).toBeInTheDocument()
  })

  it('publishes the count once the roster has settled', () => {
    const { container } = render(
      <AgentListSectionHeader variant="sidebar" label="All agents" count={7} />,
    )

    expect(screen.getByText('7')).toBeInTheDocument()
    expect(container.querySelector('.agent-roster-count-pending')).not.toBeInTheDocument()
  })
})

