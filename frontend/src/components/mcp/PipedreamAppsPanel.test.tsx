import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PipedreamAppsPanel } from './PipedreamAppsPanel'
import { fetchDiscordAgentConnections } from '../../api/discordNative'
import { fetchNativeIntegrations } from '../../api/nativeIntegrations'

vi.mock('../../api/discordNative', () => ({
  fetchDiscordAgentConnections: vi.fn(),
}))

vi.mock('../../api/mcp', () => ({
  fetchPipedreamAppSettings: vi.fn(),
}))

vi.mock('../../api/nativeIntegrations', () => ({
  fetchNativeIntegrations: vi.fn(),
}))

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <PipedreamAppsPanel
        nativeIntegrationsUrl="/console/api/native-integrations/"
        onError={vi.fn()}
      />
    </QueryClientProvider>,
  )
}

describe('PipedreamAppsPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchDiscordAgentConnections).mockReset()
    vi.mocked(fetchNativeIntegrations).mockReset()
  })

  it('shows Discord as connected when any agent has Discord connected', async () => {
    vi.mocked(fetchNativeIntegrations).mockResolvedValue({
      ownerScope: 'personal',
      ownerLabel: 'Personal',
      providers: [
        {
          providerKey: 'google_drive',
          displayName: 'Google Drive',
          description: 'Connect Google Drive.',
          authType: 'oauth2',
          icon: 'google_drive',
          apiHosts: ['www.googleapis.com'],
          scopes: [],
          connected: true,
          scope: 'personal',
          expiresAt: null,
          connectUrl: '/connect/google-drive/',
          filesUrl: '',
          pickerTokenUrl: '',
          revokeUrl: '',
        },
      ],
    })
    vi.mocked(fetchDiscordAgentConnections).mockResolvedValue({
      providerKey: 'discord',
      agents: [
        {
          agentId: 'agent-1',
          name: 'Support Agent',
          avatarUrl: '',
          connected: true,
          subscribed: false,
          skillEnabled: true,
          guildCount: 1,
          activeSubscriptionCount: 0,
        },
      ],
    })

    renderPanel()

    expect(await screen.findByText('Google Drive')).toBeInTheDocument()
    const discordChip = screen.getByText('Discord').parentElement
    expect(discordChip).not.toBeNull()
    expect(discordChip).toHaveClass('border-emerald-200')
    expect(discordChip?.querySelector('svg')).not.toBeNull()
  })
})
