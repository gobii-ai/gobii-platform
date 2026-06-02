import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type React from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AgentComposer } from './AgentComposer'
import type { PendingActionRequest } from '../../types/agentChat'
import type { InsightEvent } from '../../types/insight'

vi.mock('../../util/analytics', () => ({
  AnalyticsEvent: {
    HUMAN_INPUT_PANEL_SHOWN: 'Human Input Panel Shown',
    INSIGHT_DISMISSED: 'Insight Dismissed',
    INSIGHT_PANEL_TOGGLED: 'Insight Panel Toggled',
    INSIGHT_TAB_CLICKED: 'Insight Tab Clicked',
    UPGRADE_CHECKOUT_REDIRECTED: 'Upgrade Checkout Redirected',
  },
  track: vi.fn(),
}))

vi.mock('../../stores/subscriptionStore', () => ({
  useSubscriptionStore: () => ({
    isProprietaryMode: true,
    openUpgradeModal: vi.fn(),
    ensureAuthenticated: vi.fn(async () => true),
  }),
}))

vi.mock('./insights', () => ({
  InsightEventCard: ({ insight }: { insight: InsightEvent }) => (
    <div data-testid="insight-card">{insight.title}</div>
  ),
}))

vi.mock('./insights/GoogleDriveInsightPanel', () => ({
  GoogleDriveInsightPanel: () => (
    <div data-testid="google-drive-panel">Google Drive panel</div>
  ),
}))

vi.mock('./insights/ApolloInsightPanel', () => ({
  ApolloInsightPanel: () => (
    <div data-testid="apollo-panel">Apollo panel</div>
  ),
}))

vi.mock('./AgentIntelligenceSelector', () => ({
  AgentIntelligenceSelector: () => null,
}))

vi.mock('./ComposerPipedreamAppsControl', () => ({
  ComposerPipedreamAppsControl: () => null,
}))

function makeHumanInputAction(): PendingActionRequest {
  return {
    id: 'human-action',
    kind: 'human_input',
    count: 2,
    requests: [
      {
        id: 'question-1',
        question: 'Which account should I use?',
        options: [
          { key: 'primary', title: 'Primary account', description: 'Use the primary account.' },
        ],
        status: 'pending',
        inputMode: 'options_plus_text',
        batchId: 'batch-1',
        batchPosition: 1,
        batchSize: 2,
      },
      {
        id: 'question-2',
        question: 'Which region should I use?',
        options: [
          { key: 'us', title: 'United States', description: 'Use the United States.' },
        ],
        status: 'pending',
        inputMode: 'options_plus_text',
        batchId: 'batch-1',
        batchPosition: 2,
        batchSize: 2,
      },
    ],
  }
}

function makeFreeTextHumanInputAction(): PendingActionRequest {
  return {
    id: 'free-text-action',
    kind: 'human_input',
    count: 1,
    requests: [
      {
        id: 'free-text-question-1',
        question: 'Could you provide a quick final word?',
        options: [],
        status: 'pending',
        inputMode: 'free_text_only',
        batchId: 'free-text-batch-1',
        batchPosition: 1,
        batchSize: 1,
      },
    ],
  }
}

function makeRequestedSecretsAction(): PendingActionRequest {
  return {
    id: 'secret-action',
    kind: 'requested_secrets',
    count: 1,
    secrets: [
      {
        id: 'secret-1',
        name: 'Stripe API key',
        key: 'STRIPE_API_KEY',
        secretType: 'credential',
        domainPattern: 'stripe.com',
        description: 'Needed to finish the billing setup.',
      },
    ],
  }
}

function makeSecondRequestedSecretsAction(): PendingActionRequest {
  return {
    id: 'secret-action-2',
    kind: 'requested_secrets',
    count: 1,
    secrets: [
      {
        id: 'secret-2',
        name: 'Database password',
        key: 'DB_PASSWORD',
        secretType: 'env_var',
        domainPattern: '__gobii_env_var__',
        description: 'Needed for database access.',
      },
    ],
  }
}

function makeInsight(): InsightEvent {
  return {
    insightId: 'insight-1',
    insightType: 'burn_rate',
    priority: 1,
    title: 'Usage',
    body: 'Track usage.',
    dismissible: true,
    metadata: {
      agentName: 'Test Agent',
      todayUsage: { used: 1, limit: 10, percentUsed: 10, unlimited: false },
      monthUsage: { used: 5, limit: 100, percentUsed: 5, unlimited: false },
    },
  }
}

function renderAgentComposer(props: Partial<React.ComponentProps<typeof AgentComposer>> = {}) {
  return render(
    <AgentComposer
      agentId="agent-1"
      agentName="Test Agent"
      agentFirstName="Test"
      onSubmit={vi.fn(async () => undefined)}
      currentInsightIndex={0}
      pendingActionRequests={[]}
      insights={[]}
      insightsLoading={false}
      isProcessing={false}
      processingTasks={[]}
      {...props}
    />,
  )
}

describe('AgentComposer pending action insights panel', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'ResizeObserver', {
      configurable: true,
      value: class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    })
  })

  it('shows the insights panel for pending requests without processing or insights', () => {
    renderAgentComposer({
      pendingActionRequests: [makeRequestedSecretsAction()],
    })

    expect(screen.getByText('Needs your input')).toBeInTheDocument()
    expect(screen.getByText('1 request')).toBeInTheDocument()
    expect(screen.getByText('Stripe API key')).toBeInTheDocument()
  })

  it('auto-expands when pending requests arrive despite a collapsed insight preference', async () => {
    const { rerender } = renderAgentComposer({
      insightsPanelExpandedPreference: false,
    })

    expect(screen.queryByText('Needs your input')).not.toBeInTheDocument()

    rerender(
      <AgentComposer
        agentId="agent-1"
        agentName="Test Agent"
        agentFirstName="Test"
        onSubmit={vi.fn(async () => undefined)}
        currentInsightIndex={0}
        pendingActionRequests={[makeRequestedSecretsAction()]}
        insights={[]}
        insightsLoading={false}
        isProcessing={false}
        processingTasks={[]}
        insightsPanelExpandedPreference={false}
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('Stripe API key')).toBeInTheDocument()
    })
  })

  it('renders pending requests before regular insight content', () => {
    renderAgentComposer({
      pendingActionRequests: [makeRequestedSecretsAction()],
      insights: [makeInsight()],
      googleSheetsDriveTabEnabled: true,
    })

    expect(screen.getByText('Stripe API key')).toBeInTheDocument()
    expect(screen.queryByTestId('insight-card')).not.toBeInTheDocument()
    expect(screen.queryByTestId('google-drive-panel')).not.toBeInTheDocument()
  })

  it('keeps the normal message composer hidden for human input requests', () => {
    renderAgentComposer({
      pendingActionRequests: [makeHumanInputAction()],
    })

    expect(screen.getByText('Which account should I use?')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/^Message/)).not.toBeInTheDocument()
  })

  it('uses the regular composer for free text human input and keeps it visible when collapsed', () => {
    renderAgentComposer({
      pendingActionRequests: [makeFreeTextHumanInputAction()],
    })

    expect(screen.getByText('Could you provide a quick final word?')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/^Type your answer/)).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/^Message/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('Needs your input').closest('.composer-working-header-row') as HTMLElement)

    expect(screen.queryByText('Could you provide a quick final word?')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/^Type your answer/)).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/^Message/)).not.toBeInTheDocument()
  })

  it('submits free text human input from the regular composer', async () => {
    const handleRespondHumanInput = vi.fn(async () => undefined)

    renderAgentComposer({
      pendingActionRequests: [makeFreeTextHumanInputAction()],
      onRespondHumanInput: handleRespondHumanInput,
    })

    const textarea = screen.getByPlaceholderText(/^Type your answer/)
    fireEvent.change(textarea, { target: { value: 'done' } })
    fireEvent.submit(textarea.closest('form') as HTMLFormElement)

    await waitFor(() => {
      expect(handleRespondHumanInput).toHaveBeenCalledWith({
        requestId: 'free-text-question-1',
        freeText: 'done',
      })
    })
  })

  it('returns to the normal composer when paging from free text input to a non-human request', async () => {
    const handleSubmit = vi.fn(async () => undefined)
    const handleRespondHumanInput = vi.fn(async () => undefined)

    renderAgentComposer({
      pendingActionRequests: [makeFreeTextHumanInputAction(), makeRequestedSecretsAction()],
      onSubmit: handleSubmit,
      onRespondHumanInput: handleRespondHumanInput,
    })

    const freeTextComposer = screen.getByPlaceholderText(/^Type your answer/)
    fireEvent.change(freeTextComposer, { target: { value: 'human input draft' } })

    fireEvent.click(screen.getByRole('button', { name: 'Next pending request' }))

    expect(screen.getByText('Stripe API key')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/^Type your answer/)).not.toBeInTheDocument()

    const messageComposer = screen.getByPlaceholderText(/^Message/)
    expect(messageComposer).toHaveValue('')
    fireEvent.change(messageComposer, { target: { value: 'normal message' } })
    fireEvent.submit(messageComposer.closest('form') as HTMLFormElement)

    await waitFor(() => {
      expect(handleSubmit).toHaveBeenCalledWith('normal message', [])
    })
    expect(handleRespondHumanInput).not.toHaveBeenCalled()
  })

  it('keeps the normal message composer available for non-human pending actions', () => {
    renderAgentComposer({
      pendingActionRequests: [makeRequestedSecretsAction()],
    })

    expect(screen.getByText('Stripe API key')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/^Message/)).toBeInTheDocument()
  })

  it('shows skip planning beside the active planning status instead of a planning strip', () => {
    const handleSkipPlanning = vi.fn()

    renderAgentComposer({
      planningState: 'planning',
      isProcessing: true,
      onSkipPlanning: handleSkipPlanning,
    })

    const skipButton = screen.getByRole('button', { name: 'Skip Planning' })

    expect(screen.getByText('Test')).toBeInTheDocument()
    expect(screen.getByText(/is planning/)).toBeInTheDocument()
    expect(skipButton).toBeInTheDocument()
    expect(screen.queryByText('Planning mode')).not.toBeInTheDocument()

    fireEvent.click(skipButton)

    expect(handleSkipPlanning).toHaveBeenCalledTimes(1)
  })

  it('shows skip planning in the insights panel header when planning is idle', () => {
    const handleSkipPlanning = vi.fn()

    renderAgentComposer({
      planningState: 'planning',
      onSkipPlanning: handleSkipPlanning,
    })

    const skipButton = screen.getByRole('button', { name: 'Skip Planning' })

    expect(screen.getByText(/is planning/)).toBeInTheDocument()
    expect(skipButton).toBeInTheDocument()

    fireEvent.click(skipButton)

    expect(handleSkipPlanning).toHaveBeenCalledTimes(1)
  })

  it('shows skip planning in the pending request header during planning', () => {
    const handleSkipPlanning = vi.fn()

    renderAgentComposer({
      planningState: 'planning',
      pendingActionRequests: [makeHumanInputAction()],
      onSkipPlanning: handleSkipPlanning,
    })

    const skipButton = screen.getByRole('button', { name: 'Skip Planning' })

    expect(screen.getByText('Needs your input')).toBeInTheDocument()
    expect(skipButton).toBeInTheDocument()

    fireEvent.click(skipButton)

    expect(handleSkipPlanning).toHaveBeenCalledTimes(1)
  })

  it('pages through all pending requests, not only the active human input batch', () => {
    renderAgentComposer({
      pendingActionRequests: [
        makeHumanInputAction(),
        makeRequestedSecretsAction(),
        makeSecondRequestedSecretsAction(),
      ],
    })

    expect(screen.getByRole('button', { name: 'Next pending request' }).closest('.composer-working-header-row')).not.toBeNull()
    expect(screen.getByText('1 of 4')).toBeInTheDocument()
    expect(screen.getByText('Which account should I use?')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Next pending request' }))

    expect(screen.getByText('2 of 4')).toBeInTheDocument()
    expect(screen.getByText('Which region should I use?')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Next pending request' }))

    expect(screen.getByText('3 of 4')).toBeInTheDocument()
    expect(screen.getByText('Stripe API key')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Next pending request' }))

    expect(screen.getByText('4 of 4')).toBeInTheDocument()
    expect(screen.getByText('Database password')).toBeInTheDocument()
  })

  it('shows the Google Drive tab when the Sheets integration is enabled', () => {
    renderAgentComposer({
      googleSheetsDriveTabEnabled: true,
    })

    expect(screen.getByRole('button', { name: 'View Google Drive files' })).toBeInTheDocument()
    expect(screen.getByTestId('google-drive-panel')).toBeInTheDocument()
  })

  it('shows the Apollo tab when the Apollo native integration is enabled', () => {
    renderAgentComposer({
      apolloNativeTabEnabled: true,
    })

    expect(screen.getByRole('button', { name: 'View Apollo connection' })).toBeInTheDocument()
    expect(screen.getByTestId('apollo-panel')).toBeInTheDocument()
  })

  it('auto-selects Google Drive once when Sheets becomes enabled', async () => {
    const { rerender } = renderAgentComposer({
      insights: [makeInsight()],
      googleSheetsDriveTabEnabled: false,
    })

    expect(screen.getByTestId('insight-card')).toHaveTextContent('Usage')
    expect(screen.queryByTestId('google-drive-panel')).not.toBeInTheDocument()

    rerender(
      <AgentComposer
        agentId="agent-1"
        agentName="Test Agent"
        agentFirstName="Test"
        onSubmit={vi.fn(async () => undefined)}
        currentInsightIndex={0}
        pendingActionRequests={[]}
        insights={[makeInsight()]}
        insightsLoading={false}
        isProcessing={false}
        processingTasks={[]}
        googleSheetsDriveTabEnabled
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('google-drive-panel')).toBeInTheDocument()
    })
  })

  it('auto-selects Apollo once when Apollo native becomes enabled', async () => {
    const { rerender } = renderAgentComposer({
      insights: [makeInsight()],
      apolloNativeTabEnabled: false,
    })

    expect(screen.getByTestId('insight-card')).toHaveTextContent('Usage')
    expect(screen.queryByTestId('apollo-panel')).not.toBeInTheDocument()

    rerender(
      <AgentComposer
        agentId="agent-1"
        agentName="Test Agent"
        agentFirstName="Test"
        onSubmit={vi.fn(async () => undefined)}
        currentInsightIndex={0}
        pendingActionRequests={[]}
        insights={[makeInsight()]}
        insightsLoading={false}
        isProcessing={false}
        processingTasks={[]}
        apolloNativeTabEnabled
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('apollo-panel')).toBeInTheDocument()
    })
  })

  it('does not override a manual tab choice after Google Drive auto-selects', async () => {
    const { rerender } = renderAgentComposer({
      insights: [makeInsight()],
      googleSheetsDriveTabEnabled: false,
    })

    rerender(
      <AgentComposer
        agentId="agent-1"
        agentName="Test Agent"
        agentFirstName="Test"
        onSubmit={vi.fn(async () => undefined)}
        currentInsightIndex={0}
        pendingActionRequests={[]}
        insights={[makeInsight()]}
        insightsLoading={false}
        isProcessing={false}
        processingTasks={[]}
        googleSheetsDriveTabEnabled
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('google-drive-panel')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'View burn rate insight' }))

    expect(screen.getByTestId('insight-card')).toHaveTextContent('Usage')

    rerender(
      <AgentComposer
        agentId="agent-1"
        agentName="Test Agent"
        agentFirstName="Test"
        onSubmit={vi.fn(async () => undefined)}
        currentInsightIndex={0}
        pendingActionRequests={[]}
        insights={[makeInsight()]}
        insightsLoading={false}
        isProcessing={false}
        processingTasks={[]}
        googleSheetsDriveTabEnabled
      />,
    )

    expect(screen.getByTestId('insight-card')).toHaveTextContent('Usage')
    expect(screen.queryByTestId('google-drive-panel')).not.toBeInTheDocument()
  })

  it('does not override a manual tab choice after Apollo auto-selects', async () => {
    const { rerender } = renderAgentComposer({
      insights: [makeInsight()],
      apolloNativeTabEnabled: false,
    })

    rerender(
      <AgentComposer
        agentId="agent-1"
        agentName="Test Agent"
        agentFirstName="Test"
        onSubmit={vi.fn(async () => undefined)}
        currentInsightIndex={0}
        pendingActionRequests={[]}
        insights={[makeInsight()]}
        insightsLoading={false}
        isProcessing={false}
        processingTasks={[]}
        apolloNativeTabEnabled
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('apollo-panel')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'View burn rate insight' }))

    expect(screen.getByTestId('insight-card')).toHaveTextContent('Usage')

    rerender(
      <AgentComposer
        agentId="agent-1"
        agentName="Test Agent"
        agentFirstName="Test"
        onSubmit={vi.fn(async () => undefined)}
        currentInsightIndex={0}
        pendingActionRequests={[]}
        insights={[makeInsight()]}
        insightsLoading={false}
        isProcessing={false}
        processingTasks={[]}
        apolloNativeTabEnabled
      />,
    )

    expect(screen.getByTestId('insight-card')).toHaveTextContent('Usage')
    expect(screen.queryByTestId('apollo-panel')).not.toBeInTheDocument()
  })
})
