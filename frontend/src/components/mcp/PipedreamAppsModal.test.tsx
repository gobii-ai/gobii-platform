import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PipedreamAppsModal } from './PipedreamAppsModal'
import { fetchDiscordAgentConnections } from '../../api/discordNative'
import { fetchNativeIntegrations } from '../../api/nativeIntegrations'

vi.mock('../../api/discordNative', () => ({
  agentDiscordAppQueryKey: (agentId: string) => ['agent-discord-app', agentId],
  fetchAgentDiscordApp: vi.fn(),
  fetchAgentDiscordGuildChannels: vi.fn(),
  fetchDiscordAgentConnections: vi.fn(),
  startAgentDiscordConnect: vi.fn(),
  updateAgentDiscordSubscriptions: vi.fn(),
}))

vi.mock('../../api/mcp', () => ({
  disconnectAgentPipedreamApp: vi.fn(),
  fetchPipedreamAppAgentConnections: vi.fn(),
  searchPipedreamApps: vi.fn(),
  startAgentPipedreamAppConnect: vi.fn(),
  updatePipedreamAppSettings: vi.fn(),
}))

vi.mock('../../api/nativeIntegrations', () => ({
  fetchNativeIntegrationPickerToken: vi.fn(),
  fetchNativeIntegrations: vi.fn(),
  revokeNativeIntegration: vi.fn(),
  startNativeIntegrationConnect: vi.fn(),
}))

vi.mock('./NativeIntegrationShared', () => ({
  NativeIntegrationFilesDisclosure: () => null,
  NativeProviderIconTile: ({ provider }: { provider: { displayName: string } }) => (
    <img src="/native.svg" alt="" data-provider={provider.displayName} />
  ),
  nativeIntegrationFilesQueryKey: (provider: { providerKey: string }) => ['native-files', provider.providerKey],
  nativeOAuthContextPayload: vi.fn(),
  openGoogleDrivePicker: vi.fn(),
  openNativeOAuthPopup: vi.fn(),
  storePendingNativeOAuth: vi.fn(),
  supportsNativeIntegrationPicker: vi.fn(() => false),
  useNativeIntegrationRefreshEffects: vi.fn(),
}))

function renderModal() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <PipedreamAppsModal
        settingsUrl={null}
        searchUrl={null}
        nativeIntegrationsUrl="/console/api/native-integrations/"
        initialSettings={{
          ownerScope: 'personal',
          ownerLabel: 'Personal',
          platformApps: [],
          selectedApps: [],
          effectiveApps: [],
        }}
        onClose={vi.fn()}
        onError={vi.fn()}
      />
    </QueryClientProvider>,
  )
}

describe('PipedreamAppsModal', () => {
  beforeEach(() => {
    vi.mocked(fetchDiscordAgentConnections).mockReset()
    vi.mocked(fetchNativeIntegrations).mockReset()
  })

  it('shows Discord in the workspace app modal and opens agent connections without an agent id', async () => {
    vi.mocked(fetchNativeIntegrations).mockResolvedValue({
      ownerScope: 'personal',
      ownerLabel: 'Personal',
      providers: [],
    })
    vi.mocked(fetchDiscordAgentConnections).mockResolvedValue({
      providerKey: 'discord',
      agents: [
        {
          agentId: 'agent-1',
          name: 'Support Agent',
          avatarUrl: '',
          connected: true,
          subscribed: true,
          skillEnabled: true,
          guildCount: 1,
          activeSubscriptionCount: 2,
        },
      ],
    })

    renderModal()

    expect(await screen.findByText('Discord')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Manage Connections' }))

    await waitFor(() => {
      expect(fetchDiscordAgentConnections).toHaveBeenCalled()
    })
    expect(await screen.findByText('Support Agent')).toBeInTheDocument()
    expect(screen.getByText('1 server connected; 2 channels subscribed.')).toBeInTheDocument()
  })
})
