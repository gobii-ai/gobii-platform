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
import {
  fetchAgentSlackApp,
  fetchAgentSlackChannels,
  startAgentSlackConnect,
  updateAgentSlackSubscriptions,
  type AgentSlackApp,
} from '../../api/slackNative'

vi.mock('../../api/discordNative', () => ({
  agentDiscordAppQueryKey: (agentId: string) => ['agent-discord-app', agentId],
  disconnectDiscordNative: vi.fn(),
  fetchAgentDiscordApp: vi.fn(),
  fetchAgentDiscordGuildChannels: vi.fn(),
  startAgentDiscordConnect: vi.fn(),
  updateAgentDiscordSubscriptions: vi.fn(),
}))

vi.mock('../../api/slackNative', () => ({
  agentSlackAppQueryKey: (agentId: string) => ['agent-slack-app', agentId],
  fetchAgentSlackApp: vi.fn(),
  fetchAgentSlackChannels: vi.fn(),
  startAgentSlackConnect: vi.fn(),
  updateAgentSlackSubscriptions: vi.fn(),
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

const connectedSlackApp: AgentSlackApp = {
  providerKey: 'slack',
  displayName: 'Slack',
  description: 'Connect Slack workspaces and subscribe this agent to selected channels.',
  icon: 'slack',
  connected: true,
  subscribed: false,
  skillEnabled: true,
  workspaces: [
    {
      workspaceId: 'workspace-1',
      teamId: 'T1',
      teamName: 'Ops Slack',
      enterpriseId: '',
      enterpriseName: '',
      botUserId: 'B1',
    },
  ],
  subscriptions: [],
  activeSubscriptionCount: 0,
  workspaceCount: 1,
  connectUrl: '/console/api/native-integrations/slack/connect/',
  identityNote: 'Display identity only.',
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
    vi.mocked(fetchAgentSlackApp).mockReset()
    vi.mocked(fetchAgentSlackChannels).mockReset()
    vi.mocked(startAgentSlackConnect).mockReset()
    vi.mocked(updateAgentSlackSubscriptions).mockReset()
    vi.mocked(fetchAgentSlackApp).mockResolvedValue(connectedSlackApp)
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

    const configureButtons = await screen.findAllByRole('button', { name: 'Configure' })
    fireEvent.click(configureButtons[0])
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

  it('saves selected Slack channel subscriptions', async () => {
    vi.mocked(fetchAgentDiscordApp).mockResolvedValue(connectedDiscordApp)
    vi.mocked(fetchAgentSlackApp).mockResolvedValue(connectedSlackApp)
    vi.mocked(fetchAgentSlackChannels).mockResolvedValue({
      status: 'success',
      message: '',
      error: '',
      setupUrl: '',
      channels: [
        {
          workspaceId: 'workspace-1',
          teamId: 'T1',
          teamName: 'Ops Slack',
          channelId: 'C1',
          channelName: 'triage',
          channelType: 'public_channel',
          label: 'Ops Slack / #triage',
        },
      ],
    })
    vi.mocked(updateAgentSlackSubscriptions).mockResolvedValue({
      ...connectedSlackApp,
      subscribed: true,
      activeSubscriptionCount: 1,
    })

    renderModal()

    const configureButtons = await screen.findAllByRole('button', { name: 'Configure' })
    fireEvent.click(configureButtons[1])
    expect(screen.getByText(/does not create separate mentionable bot users/i)).toBeInTheDocument()

    const triageChannel = await screen.findByRole('checkbox', { name: /triage/i })
    fireEvent.click(triageChannel)
    fireEvent.click(screen.getByRole('button', { name: 'Save channels' }))

    await waitFor(() => {
      expect(updateAgentSlackSubscriptions).toHaveBeenCalledWith('agent-1', [
        {
          workspaceId: 'workspace-1',
          channelId: 'C1',
          channelName: 'triage',
          channelType: 'public_channel',
        },
      ])
    })
  })
})
