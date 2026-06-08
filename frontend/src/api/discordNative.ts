import { jsonFetch, jsonRequest } from './http'

export type DiscordGuildDTO = {
  guild_id: string
  name: string
  icon_hash: string
}

export type DiscordSubscriptionDTO = {
  id: string
  agent_id: string
  guild_id: string
  guild_name: string
  channel_id: string
  channel_name: string
  status: string
  last_message_at: string
}

export type DiscordChannelDTO = {
  guild_id: string
  guild_name: string
  channel_id: string
  channel_name: string
  label: string
}

type AgentDiscordAppDTO = {
  provider_key: string
  display_name: string
  description: string
  icon: string
  connected: boolean
  subscribed: boolean
  skill_enabled: boolean
  guilds: DiscordGuildDTO[]
  subscriptions: DiscordSubscriptionDTO[]
  active_subscription_count: number
  guild_count: number
  connect_url: string
  bot_invite_url: string
}

type AgentDiscordConnectDTO = {
  connect_url: string
  skill_enabled: boolean
  app: AgentDiscordAppDTO
}

type AgentDiscordChannelsDTO = {
  status: string
  message?: string
  error?: string
  bot_invite_url?: string
  channels: DiscordChannelDTO[]
}

type DiscordAgentConnectionDTO = {
  agent_id: string
  name: string
  avatar_url: string
  connected: boolean
  subscribed: boolean
  skill_enabled: boolean
  guild_count: number
  active_subscription_count: number
}

type DiscordAgentConnectionsDTO = {
  provider_key: string
  agents: DiscordAgentConnectionDTO[]
}

export type DiscordGuild = {
  guildId: string
  name: string
  iconHash: string
}

export type DiscordSubscription = {
  id: string
  agentId: string
  guildId: string
  guildName: string
  channelId: string
  channelName: string
  status: string
  lastMessageAt: string
}

export type DiscordChannel = {
  guildId: string
  guildName: string
  channelId: string
  channelName: string
  label: string
}

export type AgentDiscordApp = {
  providerKey: string
  displayName: string
  description: string
  icon: string
  connected: boolean
  subscribed: boolean
  skillEnabled: boolean
  guilds: DiscordGuild[]
  subscriptions: DiscordSubscription[]
  activeSubscriptionCount: number
  guildCount: number
  connectUrl: string
  botInviteUrl: string
}

export type AgentDiscordConnectResponse = {
  connectUrl: string
  skillEnabled: boolean
  app: AgentDiscordApp
}

export type AgentDiscordChannelsResponse = {
  status: string
  message: string
  error: string
  botInviteUrl: string
  channels: DiscordChannel[]
}

export type DiscordSubscriptionSelection = {
  guildId: string
  channelId: string
  channelName?: string
}

export type DiscordAgentConnection = {
  agentId: string
  name: string
  avatarUrl: string
  connected: boolean
  subscribed: boolean
  skillEnabled: boolean
  guildCount: number
  activeSubscriptionCount: number
}

export type DiscordAgentConnectionsResponse = {
  providerKey: string
  agents: DiscordAgentConnection[]
}

export function agentDiscordAppQueryKey(agentId: string) {
  return ['agent-discord-app', agentId] as const
}

function mapGuild(guild: DiscordGuildDTO): DiscordGuild {
  return {
    guildId: guild.guild_id,
    name: guild.name,
    iconHash: guild.icon_hash,
  }
}

function mapSubscription(subscription: DiscordSubscriptionDTO): DiscordSubscription {
  return {
    id: subscription.id,
    agentId: subscription.agent_id,
    guildId: subscription.guild_id,
    guildName: subscription.guild_name,
    channelId: subscription.channel_id,
    channelName: subscription.channel_name,
    status: subscription.status,
    lastMessageAt: subscription.last_message_at,
  }
}

function mapChannel(channel: DiscordChannelDTO): DiscordChannel {
  return {
    guildId: channel.guild_id,
    guildName: channel.guild_name,
    channelId: channel.channel_id,
    channelName: channel.channel_name,
    label: channel.label,
  }
}

function mapAgentConnection(agent: DiscordAgentConnectionDTO): DiscordAgentConnection {
  return {
    agentId: agent.agent_id,
    name: agent.name,
    avatarUrl: agent.avatar_url,
    connected: Boolean(agent.connected),
    subscribed: Boolean(agent.subscribed),
    skillEnabled: Boolean(agent.skill_enabled),
    guildCount: agent.guild_count ?? 0,
    activeSubscriptionCount: agent.active_subscription_count ?? 0,
  }
}

function mapApp(app: AgentDiscordAppDTO): AgentDiscordApp {
  return {
    providerKey: app.provider_key,
    displayName: app.display_name,
    description: app.description,
    icon: app.icon,
    connected: Boolean(app.connected),
    subscribed: Boolean(app.subscribed),
    skillEnabled: Boolean(app.skill_enabled),
    guilds: (app.guilds ?? []).map(mapGuild),
    subscriptions: (app.subscriptions ?? []).map(mapSubscription),
    activeSubscriptionCount: app.active_subscription_count ?? 0,
    guildCount: app.guild_count ?? 0,
    connectUrl: app.connect_url,
    botInviteUrl: app.bot_invite_url,
  }
}

function agentDiscordAppUrl(agentId: string): string {
  return `/console/api/agents/${agentId}/discord/app/`
}

export async function fetchAgentDiscordApp(agentId: string): Promise<AgentDiscordApp> {
  return mapApp(await jsonFetch<AgentDiscordAppDTO>(agentDiscordAppUrl(agentId)))
}

export async function fetchDiscordAgentConnections(): Promise<DiscordAgentConnectionsResponse> {
  const payload = await jsonFetch<DiscordAgentConnectionsDTO>('/console/api/discord/agents/')
  return {
    providerKey: payload.provider_key,
    agents: (payload.agents ?? []).map(mapAgentConnection),
  }
}

export async function startAgentDiscordConnect(agentId: string): Promise<AgentDiscordConnectResponse> {
  const payload = await jsonRequest<AgentDiscordConnectDTO>(
    `/console/api/agents/${agentId}/discord/connect/`,
    {
      method: 'POST',
      includeCsrf: true,
      json: {},
    },
  )
  return {
    connectUrl: payload.connect_url,
    skillEnabled: Boolean(payload.skill_enabled),
    app: mapApp(payload.app),
  }
}

export async function fetchAgentDiscordGuildChannels(
  agentId: string,
  guildId: string,
): Promise<AgentDiscordChannelsResponse> {
  const payload = await jsonFetch<AgentDiscordChannelsDTO>(
    `/console/api/agents/${agentId}/discord/guilds/${encodeURIComponent(guildId)}/channels/`,
  )
  return {
    status: payload.status,
    message: payload.message ?? '',
    error: payload.error ?? '',
    botInviteUrl: payload.bot_invite_url ?? '',
    channels: (payload.channels ?? []).map(mapChannel),
  }
}

export async function updateAgentDiscordSubscriptions(
  agentId: string,
  subscriptions: DiscordSubscriptionSelection[],
): Promise<AgentDiscordApp> {
  const payload = await jsonRequest<AgentDiscordAppDTO>(
    `/console/api/agents/${agentId}/discord/subscriptions/`,
    {
      method: 'PATCH',
      includeCsrf: true,
      json: {
        subscriptions: subscriptions.map((subscription) => ({
          guild_id: subscription.guildId,
          channel_id: subscription.channelId,
          channel_name: subscription.channelName ?? '',
        })),
      },
    },
  )
  return mapApp(payload)
}
