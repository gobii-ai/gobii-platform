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

  it('renders step credit totals from plan snapshots', () => {
    render(
      <PlanPanel
        plan={{
          ...plan,
          steps: [
            {
              id: 'step-1',
              title: 'Research sources',
              status: 'doing',
              creditsUsed: 1.25,
            },
          ],
          usage: {
            totalCredits: 2.5,
            currentStepCredits: 1.25,
          },
        }}
      />,
    )

    expect(screen.getByText('1.3 credits')).toBeInTheDocument()
    expect(screen.getByText('This task').previousSibling).toHaveTextContent('2.5')
    expect(screen.getByText('Current step').previousSibling).toHaveTextContent('1.3')
  })

  it('renders the simplified credit awareness action', () => {
    render(
      <PlanPanel
        plan={plan}
        creditAwareness={{
          agentId: 'agent-1',
          currentPlan: null,
          currentStep: null,
          dailyCredits: {
            limit: 10,
            hardLimit: 20,
            usage: 3,
            remaining: 17,
            softRemaining: 7,
            unlimited: false,
            percentUsed: 15,
            softPercentUsed: 30,
            nextResetIso: null,
            nextResetLabel: null,
            low: false,
            sliderMin: 1,
            sliderMax: 100,
            sliderLimitMax: 50,
            sliderStep: 1,
            sliderValue: 10,
            sliderEmptyValue: 100,
            standardSliderLimit: 50,
          },
          quota: {
            available: 22,
            total: 25,
            used: 3,
            used_pct: 12,
            unlimited: false,
          },
          burnRate: null,
          actions: {
            canAdjustDailyLimit: true,
            canOpenTaskPacks: true,
            canOpenUsage: true,
            canOpenIntelligenceSettings: true,
          },
        }}
        onOpenSettings={() => undefined}
        onOpenTaskPacks={() => undefined}
        onOpenUsage={() => undefined}
        onOpenIntelligenceSettings={() => undefined}
      />,
    )

    expect(screen.getByText('30%')).toBeInTheDocument()
    expect(screen.getByText('12%')).toBeInTheDocument()
    expect(screen.queryByText('Agent today')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Daily limit/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Intelligence/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Details/i })).toHaveClass('plan-panel-usage-action--details')
  })
})
