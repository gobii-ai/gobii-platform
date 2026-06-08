import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AgentPipedreamAppsModal } from './AgentPipedreamAppsModal'
import {
  fetchAgentDiscordApp,
  fetchAgentDiscordGuildChannels,
  startAgentDiscordConnect,
  updateAgentDiscordSubscriptions,
  type AgentDiscordApp,
} from '../../api/discordNative'

vi.mock('../../api/discordNative', () => ({
  agentDiscordAppQueryKey: (agentId: string) => ['agent-discord-app', agentId],
  disconnectDiscordNative: vi.fn(),
  fetchAgentDiscordApp: vi.fn(),
  fetchAgentDiscordGuildChannels: vi.fn(),
  startAgentDiscordConnect: vi.fn(),
  updateAgentDiscordSubscriptions: vi.fn(),
}))

const disconnectedDiscordApp: AgentDiscordApp = {
  providerKey: 'discord',
  displayName: 'Discord',
  description: 'Connect Discord servers and subscribe this agent to selected channels.',
  icon: 'discord',
  connected: false,
  subscribed: false,
  skillEnabled: false,
  guilds: [],
  subscriptions: [],
  activeSubscriptionCount: 0,
  guildCount: 0,
  connectUrl: '/console/api/discord/oauth/start/?agent_id=agent-1',
  botInviteUrl: 'https://discord.com/oauth2/authorize?client_id=bot',
}

const connectedDiscordApp: AgentDiscordApp = {
  ...disconnectedDiscordApp,
  connected: true,
  skillEnabled: true,
  guildCount: 1,
  guilds: [
    {
      guildId: 'guild-1',
      name: 'Ops Server',
      iconHash: '',
    },
  ],
}

function renderModal() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AgentPipedreamAppsModal
        agentId="agent-1"
        enablePipedreamApps={false}
        nativeIntegrationsUrl={null}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  )
}

describe('AgentPipedreamAppsModal Discord integration', () => {
  beforeEach(() => {
    vi.mocked(fetchAgentDiscordApp).mockReset()
    vi.mocked(fetchAgentDiscordGuildChannels).mockReset()
    vi.mocked(startAgentDiscordConnect).mockReset()
    vi.mocked(updateAgentDiscordSubscriptions).mockReset()
    vi.spyOn(window, 'open').mockImplementation(() => null)
  })

  it('renders Discord as a native app and starts OAuth connect', async () => {
    vi.mocked(fetchAgentDiscordApp).mockResolvedValue(disconnectedDiscordApp)
    vi.mocked(startAgentDiscordConnect).mockResolvedValue({
      connectUrl: 'https://discord.com/oauth2/authorize?state=oauth-state',
      skillEnabled: true,
      app: { ...disconnectedDiscordApp, skillEnabled: true },
    })

    renderModal()

    expect(await screen.findByText('Discord')).toBeInTheDocument()
    expect(screen.getByText('Native')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))

    await waitFor(() => {
      expect(startAgentDiscordConnect).toHaveBeenCalledWith('agent-1')
    })
    expect(window.open).toHaveBeenCalledWith(
      'https://discord.com/oauth2/authorize?state=oauth-state',
      '_blank',
    )
  })

  it('saves selected Discord server channel subscriptions', async () => {
    vi.mocked(fetchAgentDiscordApp).mockResolvedValue(connectedDiscordApp)
    vi.mocked(fetchAgentDiscordGuildChannels).mockResolvedValue({
      status: 'success',
      message: '',
      error: '',
      botInviteUrl: '',
      channels: [
        {
          guildId: 'guild-1',
          guildName: 'Ops Server',
          channelId: 'channel-1',
          channelName: 'general',
          label: 'Ops Server / #general',
        },
      ],
    })
    vi.mocked(updateAgentDiscordSubscriptions).mockResolvedValue({
      ...connectedDiscordApp,
      subscribed: true,
      activeSubscriptionCount: 1,
    })

    renderModal()

    fireEvent.click(await screen.findByRole('button', { name: 'Configure' }))
    expect(screen.getByRole('button', { name: 'Back' })).toBeInTheDocument()

    const generalChannel = await screen.findByRole('checkbox', { name: /general/i })
    fireEvent.click(generalChannel)
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(updateAgentDiscordSubscriptions).toHaveBeenCalledWith('agent-1', [
        {
          guildId: 'guild-1',
          channelId: 'channel-1',
          channelName: 'general',
        },
      ])
    })
  })
})
