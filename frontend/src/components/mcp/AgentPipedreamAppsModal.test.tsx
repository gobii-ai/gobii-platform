import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AgentPipedreamAppsModal } from './AgentPipedreamAppsModal'
import {
  disconnectDiscordGuild,
  fetchAgentDiscordApp,
  fetchAgentDiscordGuildChannels,
  startAgentDiscordConnect,
  updateAgentDiscordSubscriptions,
  type AgentDiscordApp,
} from '../../api/discordNative'

vi.mock('../../api/discordNative', () => ({
  agentDiscordAppQueryKey: (agentId: string) => ['agent-discord-app', agentId],
  discordContextAppQueryKey: () => ['discord-context-app'],
  disconnectDiscordGuild: vi.fn(),
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
  const result = render(
    <QueryClientProvider client={queryClient}>
      <AgentPipedreamAppsModal
        agentId="agent-1"
        enablePipedreamApps={false}
        nativeIntegrationsUrl={null}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  )
  return { ...result, queryClient }
}

describe('AgentPipedreamAppsModal Discord integration', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.mocked(disconnectDiscordGuild).mockReset()
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
      oauthRequired: true,
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
      connectUrl: '',
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

  it('removes a Discord server and refreshes context state', async () => {
    vi.mocked(fetchAgentDiscordApp).mockResolvedValue(connectedDiscordApp)
    vi.mocked(fetchAgentDiscordGuildChannels).mockResolvedValue({
      status: 'success',
      message: '',
      error: '',
      connectUrl: '',
      channels: [],
    })
    vi.mocked(disconnectDiscordGuild).mockResolvedValue({ revoked: true })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const { queryClient } = renderModal()
    const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries')

    fireEvent.click(await screen.findByRole('button', { name: 'Configure' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }))

    await waitFor(() => {
      expect(disconnectDiscordGuild).toHaveBeenCalled()
    })
    expect(vi.mocked(disconnectDiscordGuild).mock.calls[0]?.[0]).toBe('guild-1')
    expect(window.confirm).toHaveBeenCalledWith(
      'Remove Ops Server from this Gobii context? This stops every agent subscription in that server and uninstalls the Gobii bot.',
    )
    await waitFor(() => {
      expect(invalidateQueries).toHaveBeenCalledWith({
        queryKey: ['discord-context-app'],
      })
    })
  })

  it('shows Discord server removal failures', async () => {
    vi.mocked(fetchAgentDiscordApp).mockResolvedValue(connectedDiscordApp)
    vi.mocked(fetchAgentDiscordGuildChannels).mockResolvedValue({
      status: 'success',
      message: '',
      error: '',
      connectUrl: '',
      channels: [],
    })
    vi.mocked(disconnectDiscordGuild).mockRejectedValue(
      new Error('Discord server removal could not reach Discord.'),
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderModal()

    fireEvent.click(await screen.findByRole('button', { name: 'Configure' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }))

    expect(
      await screen.findByText('Discord server removal could not reach Discord.'),
    ).toBeInTheDocument()
  })
})
