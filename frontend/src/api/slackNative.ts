import { jsonFetch, jsonRequest } from './http'

type SlackWorkspacePayload = {
  workspace_id: string
  team_id: string
  team_name: string
  enterprise_id: string
  enterprise_name: string
  bot_user_id: string
}

type SlackSubscriptionPayload = {
  id: string
  agent_id: string
  workspace_id: string
  team_id: string
  team_name: string
  channel_id: string
  channel_name: string
  channel_type: string
  status: string
  last_message_at: string
}

type SlackChannelPayload = {
  workspace_id: string
  team_id: string
  team_name: string
  channel_id: string
  channel_name: string
  channel_type: string
  label: string
}

type AgentSlackAppPayload = {
  provider_key: string
  display_name: string
  description: string
  icon: string
  connected: boolean
  subscribed: boolean
  skill_enabled: boolean
  workspaces: SlackWorkspacePayload[]
  subscriptions: SlackSubscriptionPayload[]
  active_subscription_count: number
  workspace_count: number
  connect_url: string
  identity_note: string
}

type AgentSlackConnectPayload = {
  connect_url: string
  skill_enabled: boolean
  app: AgentSlackAppPayload
}

type AgentSlackChannelsPayload = {
  status: string
  message?: string
  error?: string
  setup_url?: string
  channels: SlackChannelPayload[]
}

export type SlackWorkspace = {
  workspaceId: string
  teamId: string
  teamName: string
  enterpriseId: string
  enterpriseName: string
  botUserId: string
}

export type SlackSubscription = {
  id: string
  agentId: string
  workspaceId: string
  teamId: string
  teamName: string
  channelId: string
  channelName: string
  channelType: string
  status: string
  lastMessageAt: string
}

export type SlackChannel = {
  workspaceId: string
  teamId: string
  teamName: string
  channelId: string
  channelName: string
  channelType: string
  label: string
}

export type AgentSlackApp = {
  providerKey: string
  displayName: string
  description: string
  icon: string
  connected: boolean
  subscribed: boolean
  skillEnabled: boolean
  workspaces: SlackWorkspace[]
  subscriptions: SlackSubscription[]
  activeSubscriptionCount: number
  workspaceCount: number
  connectUrl: string
  identityNote: string
}

export type AgentSlackConnectResponse = {
  connectUrl: string
  skillEnabled: boolean
  app: AgentSlackApp
}

export type AgentSlackChannelsResponse = {
  status: string
  message: string
  error: string
  setupUrl: string
  channels: SlackChannel[]
}

export type SlackSubscriptionSelection = {
  workspaceId: string
  channelId: string
  channelName?: string
  channelType?: string
}

export function agentSlackAppQueryKey(agentId: string) {
  return ['agent-slack-app', agentId] as const
}

function mapApp(app: AgentSlackAppPayload): AgentSlackApp {
  return {
    providerKey: app.provider_key,
    displayName: app.display_name,
    description: app.description,
    icon: app.icon,
    connected: Boolean(app.connected),
    subscribed: Boolean(app.subscribed),
    skillEnabled: Boolean(app.skill_enabled),
    workspaces: (app.workspaces ?? []).map((workspace) => ({
      workspaceId: workspace.workspace_id,
      teamId: workspace.team_id,
      teamName: workspace.team_name,
      enterpriseId: workspace.enterprise_id,
      enterpriseName: workspace.enterprise_name,
      botUserId: workspace.bot_user_id,
    })),
    subscriptions: (app.subscriptions ?? []).map((subscription) => ({
      id: subscription.id,
      agentId: subscription.agent_id,
      workspaceId: subscription.workspace_id,
      teamId: subscription.team_id,
      teamName: subscription.team_name,
      channelId: subscription.channel_id,
      channelName: subscription.channel_name,
      channelType: subscription.channel_type,
      status: subscription.status,
      lastMessageAt: subscription.last_message_at,
    })),
    activeSubscriptionCount: app.active_subscription_count ?? 0,
    workspaceCount: app.workspace_count ?? 0,
    connectUrl: app.connect_url,
    identityNote: app.identity_note ?? '',
  }
}

export async function fetchAgentSlackApp(agentId: string): Promise<AgentSlackApp> {
  return mapApp(await jsonFetch<AgentSlackAppPayload>(`/console/api/agents/${agentId}/slack/app/`))
}

export async function startAgentSlackConnect(agentId: string): Promise<AgentSlackConnectResponse> {
  const payload = await jsonRequest<AgentSlackConnectPayload>(
    `/console/api/agents/${agentId}/slack/connect/`,
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

export async function fetchAgentSlackChannels(agentId: string, query = ''): Promise<AgentSlackChannelsResponse> {
  const suffix = query ? `?q=${encodeURIComponent(query)}` : ''
  const payload = await jsonFetch<AgentSlackChannelsPayload>(`/console/api/agents/${agentId}/slack/channels/${suffix}`)
  return {
    status: payload.status,
    message: payload.message ?? '',
    error: payload.error ?? '',
    setupUrl: payload.setup_url ?? '',
    channels: (payload.channels ?? []).map((channel) => ({
      workspaceId: channel.workspace_id,
      teamId: channel.team_id,
      teamName: channel.team_name,
      channelId: channel.channel_id,
      channelName: channel.channel_name,
      channelType: channel.channel_type,
      label: channel.label,
    })),
  }
}

export async function updateAgentSlackSubscriptions(
  agentId: string,
  subscriptions: SlackSubscriptionSelection[],
): Promise<AgentSlackApp> {
  const payload = await jsonRequest<AgentSlackAppPayload>(
    `/console/api/agents/${agentId}/slack/subscriptions/`,
    {
      method: 'POST',
      includeCsrf: true,
      json: {
        subscriptions: subscriptions.map((subscription) => ({
          workspace_id: subscription.workspaceId,
          channel_id: subscription.channelId,
          channel_name: subscription.channelName ?? '',
          channel_type: subscription.channelType ?? '',
        })),
      },
    },
  )
  return mapApp(payload)
}
