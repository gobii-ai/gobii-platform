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

vi.mock('./insights/HubSpotInsightPanel', () => ({
  HubSpotInsightPanel: () => (
    <div data-testid="hubspot-panel">HubSpot panel</div>
  ),
}))

vi.mock('./insights/DiscordInsightPanel', () => ({
  DiscordInsightPanel: () => (
    <div data-testid="discord-panel">Discord panel</div>
  ),
}))

vi.mock('./AgentIntelligenceSelector', () => ({
  AgentIntelligenceSelector: () => null,
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

function makeContactRequestsAction(): PendingActionRequest {
  return {
    id: 'contact-action',
    kind: 'contact_requests',
    count: 1,
    requests: [
      {
        id: 'contact-1',
        channel: 'email',
        address: 'customer@example.com',
        name: 'Customer',
        reason: 'Needs a status update.',
        purpose: 'Support',
        allowInbound: true,
        allowOutbound: true,
      },
    ],
  }
}

function makeSpawnRequestAction(): PendingActionRequest {
  return {
    id: 'spawn-action',
    kind: 'spawn_request',
    requestId: 'spawn-request-1',
    requestedCharter: 'Handle follow-up research.',
    handoffMessage: 'Continue from the current task.',
    requestReason: 'Parallel research would help.',
    decisionApiUrl: '/console/api/spawn-request/1/',
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
    expect(screen.getByRole('button', { name: 'View 1 pending credentials request' })).toBeInTheDocument()
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

  it('renders pending request tabs alongside regular insight tabs', () => {
    renderAgentComposer({
      pendingActionRequests: [makeRequestedSecretsAction()],
      insights: [makeInsight()],
      googleSheetsDriveTabEnabled: true,
    })

    expect(screen.getByText('Stripe API key')).toBeInTheDocument()
    expect(screen.queryByTestId('insight-card')).not.toBeInTheDocument()
    expect(screen.queryByTestId('google-drive-panel')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'View burn rate insight' }))

    expect(screen.getByTestId('insight-card')).toHaveTextContent('Usage')

    fireEvent.click(screen.getByRole('button', { name: 'View Google Drive files' }))

    expect(screen.getByTestId('google-drive-panel')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'View 1 pending credentials request' }))

    expect(screen.getByText('Stripe API key')).toBeInTheDocument()
  })

  it('keeps the normal message composer hidden for human input requests', () => {
    renderAgentComposer({
      pendingActionRequests: [makeHumanInputAction()],
    })

    expect(screen.getByText('Which account should I use?')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/^Message/)).not.toBeInTheDocument()
  })

  it('uses the regular composer for free text human input only while expanded', () => {
    renderAgentComposer({
      pendingActionRequests: [makeFreeTextHumanInputAction()],
    })

    expect(screen.getByText('Could you provide a quick final word?')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/^Type your answer/)).toBeInTheDocument()
    expect(screen.getByText('Dismiss')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/^Message/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('Needs your input').closest('.composer-working-header-row') as HTMLElement)

    expect(screen.queryByText('Could you provide a quick final word?')).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/^Type your answer/)).not.toBeInTheDocument()
    expect(screen.queryByText('Dismiss')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/^Message/)).toBeInTheDocument()
  })

  it('hides pending request navigation when the panel is collapsed', () => {
    renderAgentComposer({
      pendingActionRequests: [makeHumanInputAction()],
    })

    expect(screen.getByRole('button', { name: 'Next pending request' })).toBeInTheDocument()

    fireEvent.click(screen.getByText('Needs your input').closest('.composer-working-header-row') as HTMLElement)

    expect(screen.queryByRole('button', { name: 'Next pending request' })).not.toBeInTheDocument()
    expect(screen.queryByText('1 of 2')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/^Message/)).toBeInTheDocument()
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

  it('returns to the normal composer when paging from free text input to a credential request', async () => {
    const handleSubmit = vi.fn(async () => undefined)
    const handleRespondHumanInput = vi.fn(async () => undefined)

    renderAgentComposer({
      pendingActionRequests: [makeFreeTextHumanInputAction(), makeRequestedSecretsAction()],
      onSubmit: handleSubmit,
      onRespondHumanInput: handleRespondHumanInput,
    })

    const freeTextComposer = screen.getByPlaceholderText(/^Type your answer/)
    fireEvent.change(freeTextComposer, { target: { value: 'human input draft' } })

    fireEvent.click(screen.getByRole('button', { name: 'View 1 pending credentials request' }))

    expect(screen.getByText('Stripe API key')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/^Type your answer/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()

    const messageComposer = screen.getByPlaceholderText(/^Message/)
    expect(messageComposer).toHaveValue('')
    fireEvent.change(messageComposer, { target: { value: 'normal message' } })
    fireEvent.submit(messageComposer.closest('form') as HTMLFormElement)

    await waitFor(() => {
      expect(handleSubmit).toHaveBeenCalledWith('normal message', [])
    })
    expect(handleRespondHumanInput).not.toHaveBeenCalled()
  })

  it('keeps the normal message composer available for credential pending actions', () => {
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

  it('pages through pending requests within the active tab category', () => {
    renderAgentComposer({
      pendingActionRequests: [
        makeHumanInputAction(),
        makeRequestedSecretsAction(),
        makeSecondRequestedSecretsAction(),
      ],
    })

    expect(screen.getByRole('button', { name: 'Next pending request' }).closest('.composer-working-header-row')).not.toBeNull()
    expect(screen.getByText('1 of 2')).toBeInTheDocument()
    expect(screen.getByText('Which account should I use?')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Next pending request' }))

    expect(screen.getByText('2 of 2')).toBeInTheDocument()
    expect(screen.getByText('Which region should I use?')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'View 2 pending credentials requests' }))

    expect(screen.getByText('1 of 2')).toBeInTheDocument()
    expect(screen.getByText('Stripe API key')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Next pending request' }))

    expect(screen.getByText('2 of 2')).toBeInTheDocument()
    expect(screen.getByText('Database password')).toBeInTheDocument()
  })

  it('auto-selects a new pending tab from a regular insight tab', async () => {
    const { rerender } = renderAgentComposer({
      insights: [makeInsight()],
    })

    expect(screen.getByTestId('insight-card')).toHaveTextContent('Usage')

    rerender(
      <AgentComposer
        agentId="agent-1"
        agentName="Test Agent"
        agentFirstName="Test"
        onSubmit={vi.fn(async () => undefined)}
        currentInsightIndex={0}
        pendingActionRequests={[makeContactRequestsAction()]}
        insights={[makeInsight()]}
        insightsLoading={false}
        isProcessing={false}
        processingTasks={[]}
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('Customer')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('insight-card')).not.toBeInTheDocument()
  })

  it.each([
    {
      name: 'Questions',
      initialActions: [makeHumanInputAction()],
      nextActions: [makeHumanInputAction(), makeRequestedSecretsAction()],
      expectedText: 'Which account should I use?',
      unexpectedText: 'Stripe API key',
    },
    {
      name: 'Credentials',
      initialActions: [makeRequestedSecretsAction()],
      nextActions: [makeRequestedSecretsAction(), makeContactRequestsAction()],
      expectedText: 'Stripe API key',
      unexpectedText: 'Customer',
    },
    {
      name: 'Contacts',
      initialActions: [makeContactRequestsAction()],
      nextActions: [makeContactRequestsAction(), makeRequestedSecretsAction()],
      expectedText: 'Customer',
      unexpectedText: 'Stripe API key',
    },
    {
      name: 'Agents',
      initialActions: [makeSpawnRequestAction()],
      nextActions: [makeSpawnRequestAction(), makeContactRequestsAction()],
      expectedText: 'Handle follow-up research.',
      unexpectedText: 'Customer',
    },
  ])('does not auto-switch away from the active $name pending tab', async ({
    initialActions,
    nextActions,
    expectedText,
    unexpectedText,
  }) => {
    const { rerender } = renderAgentComposer({
      pendingActionRequests: initialActions,
    })

    expect(screen.getByText(expectedText)).toBeInTheDocument()

    rerender(
      <AgentComposer
        agentId="agent-1"
        agentName="Test Agent"
        agentFirstName="Test"
        onSubmit={vi.fn(async () => undefined)}
        currentInsightIndex={0}
        pendingActionRequests={nextActions}
        insights={[]}
        insightsLoading={false}
        isProcessing={false}
        processingTasks={[]}
      />,
    )

    await waitFor(() => {
      expect(screen.getByText(expectedText)).toBeInTheDocument()
    })
    expect(screen.queryByText(unexpectedText)).not.toBeInTheDocument()
  })

  const nativeTabCases = [
    {
      name: 'Google Drive',
      ariaLabel: 'View Google Drive files',
      panelTestId: 'google-drive-panel',
      enabledProps: { googleSheetsDriveTabEnabled: true },
      disabledProps: { googleSheetsDriveTabEnabled: false },
    },
    {
      name: 'Apollo',
      ariaLabel: 'View Apollo connection',
      panelTestId: 'apollo-panel',
      enabledProps: { apolloNativeTabEnabled: true },
      disabledProps: { apolloNativeTabEnabled: false },
    },
    {
      name: 'HubSpot',
      ariaLabel: 'View HubSpot connection',
      panelTestId: 'hubspot-panel',
      enabledProps: { hubspotNativeTabEnabled: true },
      disabledProps: { hubspotNativeTabEnabled: false },
    },
    {
      name: 'Discord',
      ariaLabel: 'View Discord connection',
      panelTestId: 'discord-panel',
      enabledProps: { discordNativeTabEnabled: true },
      disabledProps: { discordNativeTabEnabled: false },
    },
  ] as const

  it.each(nativeTabCases)('shows the $name native tab when enabled', ({ ariaLabel, enabledProps, panelTestId }) => {
    renderAgentComposer(enabledProps)

    expect(screen.getByRole('button', { name: ariaLabel })).toBeInTheDocument()
    expect(screen.getByTestId(panelTestId)).toBeInTheDocument()
  })

  it.each(nativeTabCases)('auto-selects $name once when it becomes enabled', async ({ disabledProps, enabledProps, panelTestId }) => {
    const { rerender } = renderAgentComposer({
      insights: [makeInsight()],
      ...disabledProps,
    })

    expect(screen.getByTestId('insight-card')).toHaveTextContent('Usage')
    expect(screen.queryByTestId(panelTestId)).not.toBeInTheDocument()

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
        {...enabledProps}
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId(panelTestId)).toBeInTheDocument()
    })
  })

  it('auto-selects the first native tab when multiple tabs become enabled together', async () => {
    const { rerender } = renderAgentComposer({
      insights: [makeInsight()],
      googleSheetsDriveTabEnabled: false,
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
        googleSheetsDriveTabEnabled
        apolloNativeTabEnabled
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('google-drive-panel')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('apollo-panel')).not.toBeInTheDocument()
  })

  it.each(nativeTabCases)('does not override a manual tab choice after $name auto-selects', async ({ disabledProps, enabledProps, panelTestId }) => {
    const { rerender } = renderAgentComposer({
      insights: [makeInsight()],
      ...disabledProps,
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
        {...enabledProps}
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId(panelTestId)).toBeInTheDocument()
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
        {...enabledProps}
      />,
    )

    expect(screen.getByTestId('insight-card')).toHaveTextContent('Usage')
    expect(screen.queryByTestId(panelTestId)).not.toBeInTheDocument()
  })
})
