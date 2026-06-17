import { jsonFetch, jsonRequest } from './http'

type TelegramChatPayload = {
  id: string
  agent_id: string
  chat_id: string
  chat_type: string
  message_thread_id: string
  title: string
  username: string
  status: string
  last_message_at: string
}

type AgentTelegramAppPayload = {
  provider_key: string
  display_name: string
  description: string
  icon: string
  connected: boolean
  subscribed: boolean
  skill_enabled: boolean
  user_linked: boolean
  status: string
  error?: string
  bot_username: string
  bot_display_name: string
  profile_sync_status: string
  profile_sync_error: string
  manager_link_url: string
  create_bot_url: string
  chats: TelegramChatPayload[]
  active_chat_count: number
}

type AgentTelegramConnectPayload = {
  status: string
  manager_link_url: string
  create_bot_url: string
  user_linked: boolean
  suggested_username: string
  suggested_name: string
  message: string
  app: AgentTelegramAppPayload
}

export type TelegramChatBinding = {
  id: string
  agentId: string
  chatId: string
  chatType: string
  messageThreadId: string
  title: string
  username: string
  status: string
  lastMessageAt: string
}

export type AgentTelegramApp = {
  providerKey: string
  displayName: string
  description: string
  icon: string
  connected: boolean
  subscribed: boolean
  skillEnabled: boolean
  userLinked: boolean
  status: string
  error: string
  botUsername: string
  botDisplayName: string
  profileSyncStatus: string
  profileSyncError: string
  managerLinkUrl: string
  createBotUrl: string
  chats: TelegramChatBinding[]
  activeChatCount: number
}

export type AgentTelegramConnectResponse = {
  status: string
  managerLinkUrl: string
  createBotUrl: string
  userLinked: boolean
  suggestedUsername: string
  suggestedName: string
  message: string
  app: AgentTelegramApp
}

export function agentTelegramAppQueryKey(agentId: string) {
  return ['agent-telegram-app', agentId] as const
}

function mapApp(app: AgentTelegramAppPayload): AgentTelegramApp {
  return {
    providerKey: app.provider_key,
    displayName: app.display_name,
    description: app.description,
    icon: app.icon,
    connected: Boolean(app.connected),
    subscribed: Boolean(app.subscribed),
    skillEnabled: Boolean(app.skill_enabled),
    userLinked: Boolean(app.user_linked),
    status: app.status,
    error: app.error ?? '',
    botUsername: app.bot_username,
    botDisplayName: app.bot_display_name,
    profileSyncStatus: app.profile_sync_status,
    profileSyncError: app.profile_sync_error,
    managerLinkUrl: app.manager_link_url,
    createBotUrl: app.create_bot_url,
    activeChatCount: app.active_chat_count ?? 0,
    chats: (app.chats ?? []).map((chat) => ({
      id: chat.id,
      agentId: chat.agent_id,
      chatId: chat.chat_id,
      chatType: chat.chat_type,
      messageThreadId: chat.message_thread_id,
      title: chat.title,
      username: chat.username,
      status: chat.status,
      lastMessageAt: chat.last_message_at,
    })),
  }
}

export async function fetchAgentTelegramApp(agentId: string): Promise<AgentTelegramApp> {
  return mapApp(await jsonFetch<AgentTelegramAppPayload>(`/console/api/agents/${agentId}/telegram/app/`))
}

export async function startAgentTelegramConnect(agentId: string): Promise<AgentTelegramConnectResponse> {
  const payload = await jsonRequest<AgentTelegramConnectPayload>(
    `/console/api/agents/${agentId}/telegram/connect/`,
    {
      method: 'POST',
      includeCsrf: true,
      json: {},
    },
  )
  return {
    status: payload.status,
    managerLinkUrl: payload.manager_link_url,
    createBotUrl: payload.create_bot_url,
    userLinked: Boolean(payload.user_linked),
    suggestedUsername: payload.suggested_username,
    suggestedName: payload.suggested_name,
    message: payload.message,
    app: mapApp(payload.app),
  }
}

export async function syncAgentTelegramProfile(agentId: string): Promise<AgentTelegramApp> {
  const payload = await jsonRequest<{ app: AgentTelegramAppPayload }>(
    `/console/api/agents/${agentId}/telegram/sync-profile/`,
    {
      method: 'POST',
      includeCsrf: true,
      json: {},
    },
  )
  return mapApp(payload.app)
}

export async function disconnectAgentTelegram(agentId: string): Promise<AgentTelegramApp> {
  const payload = await jsonRequest<{ app: AgentTelegramAppPayload }>(
    `/console/api/agents/${agentId}/telegram/disconnect/`,
    {
      method: 'POST',
      includeCsrf: true,
      json: {},
    },
  )
  return mapApp(payload.app)
}
