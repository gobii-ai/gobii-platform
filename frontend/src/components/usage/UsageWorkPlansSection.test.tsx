import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { parseDate } from '@internationalized/date'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { UsageWorkPlansSection } from './UsageWorkPlansSection'
import { fetchUsageWorkPlans } from './api'

vi.mock('./api', () => ({
  fetchUsageWorkPlans: vi.fn(),
}))

const fetchUsageWorkPlansMock = vi.mocked(fetchUsageWorkPlans)
const range = {
  start: parseDate('2026-05-01'),
  end: parseDate('2026-05-20'),
}

function renderSection() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <UsageWorkPlansSection effectiveRange={range} fallbackRange={null} agentIds={[]} />
    </QueryClientProvider>,
  )
}

describe('UsageWorkPlansSection', () => {
  beforeEach(() => {
    fetchUsageWorkPlansMock.mockReset()
  })

  it('renders work plan rows with step credit breakdowns', async () => {
    fetchUsageWorkPlansMock.mockResolvedValueOnce({
      plans: [
        {
          id: 'plan-1',
          agentId: 'agent-1',
          agentName: 'Research Agent',
          status: 'active',
          startedAt: '2026-05-20T12:00:00Z',
          completedAt: null,
          creditsUsed: 2.5,
          steps: [
            {
              id: 'step-1',
              title: 'Research sources',
              status: 'doing',
              creditsUsed: 1.5,
              startedAt: null,
              completedAt: null,
            },
          ],
        },
      ],
    })

    renderSection()

    expect(await screen.findByText('Research Agent')).toBeInTheDocument()
    expect(screen.getAllByText('Research sources')).toHaveLength(2)
    expect(screen.getByText('2.5 credits')).toBeInTheDocument()
    expect(screen.getByText('1.5')).toBeInTheDocument()
  })

  it('renders an empty state', async () => {
    fetchUsageWorkPlansMock.mockResolvedValueOnce({ plans: [] })

    renderSection()

    expect(await screen.findByText('No plan-attributed work in this range.')).toBeInTheDocument()
  })

  it('renders an error state', async () => {
    fetchUsageWorkPlansMock.mockRejectedValueOnce(new Error('Unable to load work usage.'))

    renderSection()

    await waitFor(() => {
      expect(screen.getByText('Unable to load work usage.')).toBeInTheDocument()
    })
  })
})
