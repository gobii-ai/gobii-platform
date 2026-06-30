import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BurnRateInsight } from './BurnRateInsight'
import type { InsightEvent } from '../../../types/insight'

describe('BurnRateInsight', () => {
  it('renders only actual usage cards even when forecast metadata is present', () => {
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
        forecast: {
          perRunCredits: 6,
          dailyCredits: 12,
          monthlyCredits: 360,
          warningLevel: 'none',
          estimatedAt: '2026-06-30T12:00:00Z',
        },
      } as InsightEvent['metadata'],
    }

    render(<BurnRateInsight insight={insight} />)

    expect(screen.queryByLabelText('Estimated credit usage')).not.toBeInTheDocument()
    expect(screen.queryByText('Estimated cost')).not.toBeInTheDocument()
    expect(screen.getByText('Today').closest('.usage-gauge-card')).toHaveClass('usage-gauge-card--today')
    expect(screen.getByText('This month').closest('.usage-gauge-card')).toHaveClass('usage-gauge-card--month')
  })
})
