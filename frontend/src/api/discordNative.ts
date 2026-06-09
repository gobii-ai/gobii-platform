import { jsonFetch, jsonRequest } from './http'

type DiscordGuildPayload = {
  guild_id: string
  name: string
  icon_hash: string
}

type DiscordSubscriptionPayload = {
  id: string
  agent_id: string
  guild_id: string
  guild_name: string
  channel_id: string
  channel_name: string
  status: string
  last_message_at: string
}

type DiscordChannelPayload = {
  guild_id: string
  guild_name: string
  channel_id: string
  channel_name: string
  label: string
}

type AgentDiscordAppPayload = {
  provider_key: string
  display_name: string
  description: string
  icon: string
  connected: boolean
  subscribed: boolean
  skill_enabled: boolean
  guilds: DiscordGuildPayload[]
  subscriptions: DiscordSubscriptionPayload[]
  active_subscription_count: number
  guild_count: number
  connect_url: string
  bot_invite_url: string
}

type AgentDiscordConnectPayload = {
  connect_url: string
  skill_enabled: boolean
  app: AgentDiscordAppPayload
}

type AgentDiscordChannelsPayload = {
  status: string
  message?: string
  error?: string
  bot_invite_url?: string
  channels: DiscordChannelPayload[]
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

export function agentDiscordAppQueryKey(agentId: string) {
  return ['agent-discord-app', agentId] as const
}

function mapApp(app: AgentDiscordAppPayload): AgentDiscordApp {
  return {
    providerKey: app.provider_key,
    displayName: app.display_name,
    description: app.description,
    icon: app.icon,
    connected: Boolean(app.connected),
    subscribed: Boolean(app.subscribed),
    skillEnabled: Boolean(app.skill_enabled),
    guilds: (app.guilds ?? []).map((guild) => ({
      guildId: guild.guild_id,
      name: guild.name,
      iconHash: guild.icon_hash,
    })),
    subscriptions: (app.subscriptions ?? []).map((subscription) => ({
      id: subscription.id,
      agentId: subscription.agent_id,
      guildId: subscription.guild_id,
      guildName: subscription.guild_name,
      channelId: subscription.channel_id,
      channelName: subscription.channel_name,
      status: subscription.status,
      lastMessageAt: subscription.last_message_at,
    })),
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
  return mapApp(await jsonFetch<AgentDiscordAppPayload>(agentDiscordAppUrl(agentId)))
}

export async function startAgentDiscordConnect(agentId: string): Promise<AgentDiscordConnectResponse> {
  const payload = await jsonRequest<AgentDiscordConnectPayload>(
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

export async function disconnectDiscordNative(): Promise<{ revoked: boolean }> {
  return jsonRequest<{ revoked: boolean }>('/console/api/discord/disconnect/', {
    method: 'POST',
    includeCsrf: true,
    json: {},
  })
}

export async function fetchAgentDiscordGuildChannels(
  agentId: string,
  guildId: string,
): Promise<AgentDiscordChannelsResponse> {
  const payload = await jsonFetch<AgentDiscordChannelsPayload>(
    `/console/api/agents/${agentId}/discord/guilds/${encodeURIComponent(guildId)}/channels/`,
  )
  return {
    status: payload.status,
    message: payload.message ?? '',
    error: payload.error ?? '',
    botInviteUrl: payload.bot_invite_url ?? '',
    channels: (payload.channels ?? []).map((channel) => ({
      guildId: channel.guild_id,
      guildName: channel.guild_name,
      channelId: channel.channel_id,
      channelName: channel.channel_name,
      label: channel.label,
    })),
  }
}

export async function updateAgentDiscordSubscriptions(
  agentId: string,
  subscriptions: DiscordSubscriptionSelection[],
): Promise<AgentDiscordApp> {
  const payload = await jsonRequest<AgentDiscordAppPayload>(
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
