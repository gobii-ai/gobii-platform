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
  })
})
