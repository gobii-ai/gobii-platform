import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { BurnRateInsight } from './BurnRateInsight'
import type { InsightEvent } from '../../../types/insight'

describe('BurnRateInsight', () => {
  const insight: InsightEvent = {
    insightId: 'burn-rate-test',
    insightType: 'burn_rate',
    priority: 1,
    title: 'Credit usage',
    body: 'Usage details',
    dismissible: true,
    metadata: {
      agentName: 'Forecast Agent',
      todayUsage: {
        used: 10,
        limit: 100,
        percentUsed: 10,
        unlimited: false,
      },
      monthUsage: {
        used: 30,
        limit: 1000,
        percentUsed: 3,
        unlimited: false,
      },
    },
  }

  it('renders usage cards without a forecast warning by default', () => {
    render(<BurnRateInsight insight={insight} />)

    expect(screen.queryByLabelText('Estimated credit usage')).not.toBeInTheDocument()
    expect(screen.queryByText('Estimated cost')).not.toBeInTheDocument()
    expect(screen.queryByText('Monthly credits may run out')).not.toBeInTheDocument()
    expect(screen.getByText('Today').closest('.usage-gauge-card')).toHaveClass('usage-gauge-card--today')
    expect(screen.getByText('This month').closest('.usage-gauge-card')).toHaveClass('usage-gauge-card--month')
  })

  it('renders a monthly forecast capacity warning with an add credits action', () => {
    const onOpenTaskPacks = vi.fn()

    render(
      <BurnRateInsight
        insight={insight}
        onOpenTaskPacks={onOpenTaskPacks}
        forecastCapacityWarning={{
          scope: 'monthly',
          estimateType: 'monthly',
          estimatedCredits: 360,
          remainingCredits: 3,
        }}
      />,
    )

    expect(screen.getByText('Not enough monthly credits')).toBeInTheDocument()
    expect(screen.getByText('This month needs 360 credits.')).toBeInTheDocument()
    screen.getByRole('button', { name: 'Add credits' }).click()
    expect(onOpenTaskPacks).toHaveBeenCalledTimes(1)
  })

  it('renders a daily forecast capacity warning with an adjust action', () => {
    const onOpenQuickSettings = vi.fn()

    render(
      <BurnRateInsight
        insight={insight}
        onOpenQuickSettings={onOpenQuickSettings}
        forecastCapacityWarning={{
          scope: 'daily',
          estimateType: 'per_run',
          estimatedCredits: 15,
          remainingCredits: 4,
        }}
      />,
    )

    expect(screen.getByText('Daily limit too low')).toBeInTheDocument()
    expect(screen.getByText('Next run needs 15 credits.')).toBeInTheDocument()
    screen.getByRole('button', { name: 'Adjust limit' }).click()
    expect(onOpenQuickSettings).toHaveBeenCalledTimes(1)
  })

  it('renders a daily limit reached warning when today usage exceeds the limit', () => {
    const onOpenQuickSettings = vi.fn()

    render(
      <BurnRateInsight
        insight={{
          ...insight,
          metadata: {
            ...insight.metadata,
            todayUsage: {
              used: 2.25,
              limit: 2,
              percentUsed: 100,
              unlimited: false,
            },
          },
        }}
        onOpenQuickSettings={onOpenQuickSettings}
      />,
    )

    expect(screen.getByText('Daily limit reached')).toBeInTheDocument()
    expect(screen.getByText('Increase the limit to let this agent keep running today.')).toBeInTheDocument()
    screen.getByRole('button', { name: 'Adjust limit' }).click()
    expect(onOpenQuickSettings).toHaveBeenCalledTimes(1)
  })
})
