import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PlanPanel } from './PlanPanel'
import type { PlanSnapshot } from '../../types/agentChat'

const plan: PlanSnapshot = {
  todoCount: 0,
  doingCount: 1,
  doneCount: 0,
  todoTitles: [],
  doingTitles: ['Research sources'],
  doneTitles: [],
  files: [],
  messages: [],
}

describe('PlanPanel', () => {
  it('shows a pause icon for doing work when the agent is idle', () => {
    const { container } = render(<PlanPanel plan={plan} isAgentWorking={false} />)

    const row = screen.getByText('Research sources').closest('.plan-panel-task')
    expect(row).toHaveAttribute('data-status', 'doing')
    expect(row).toHaveAttribute('data-work-state', 'paused')
    expect(container.querySelector('.lucide-circle-pause')).toBeInTheDocument()
    expect(container.querySelector('.lucide-loader-circle')).not.toBeInTheDocument()
  })

  it('keeps the spinner icon for doing work while the agent is active', () => {
    const { container } = render(<PlanPanel plan={plan} isAgentWorking />)

    const row = screen.getByText('Research sources').closest('.plan-panel-task')
    expect(row).toHaveAttribute('data-work-state', 'active')
    expect(container.querySelector('.lucide-loader-circle')).toBeInTheDocument()
    expect(container.querySelector('.lucide-circle-pause')).not.toBeInTheDocument()
    expect(screen.queryByText(/Est\./i)).not.toBeInTheDocument()
    expect(screen.queryByText(/credits/i)).not.toBeInTheDocument()
  })

  it('shows task credit estimates when a forecast is available', () => {
    render(
      <PlanPanel
        plan={plan}
        isAgentWorking
        creditForecast={{
          perRunCredits: 5,
          dailyCredits: 12.5,
          monthlyCredits: 250,
          warningLevel: 'medium',
          estimatedAt: '2026-07-07T18:00:00Z',
        }}
      />,
    )

    expect(screen.getByLabelText('Estimated task credits')).toBeInTheDocument()
    expect(screen.getByText('Estimated Usage')).toBeInTheDocument()
    expect(screen.getByText('5 credits')).toBeInTheDocument()
    expect(screen.getByText('/ current plan')).toBeInTheDocument()
    expect(screen.getByText('12.5 credits')).toBeInTheDocument()
    expect(screen.getByText('/ day')).toBeInTheDocument()
    expect(screen.getByText('250 credits')).toBeInTheDocument()
    expect(screen.getByText('/ month')).toBeInTheDocument()
  })
})
