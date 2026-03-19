export interface SlackSettingsPayload {
  agent_id: string
  endpoint_address: string
  workspace_id: string
  channel_id: string
  thread_policy: 'auto' | 'always' | 'never'
  is_enabled: boolean
  has_bot_token: boolean
  connection_last_ok_at: string | null
  connection_error: string
  global_slack_enabled: boolean
  global_slack_disabled_reason: string
}

export interface SlackSettingsSaveRequest {
  workspace_id?: string
  channel_id?: string
  thread_policy?: string
  is_enabled?: boolean
  bot_token?: string
  clear_bot_token?: boolean
}

export interface SlackTestResult {
  ok: boolean
  error?: string
  team?: string
  bot_user_id?: string
  team_id?: string
}

function getCsrfToken(): string {
  const el = document.querySelector<HTMLInputElement>('[name=csrfmiddlewaretoken]')
  if (el) return el.value
  const match = document.cookie.match(/csrftoken=([^;]+)/)
  return match ? match[1] : ''
}

export async function fetchSlackSettings(url: string): Promise<SlackSettingsPayload> {
  const res = await fetch(url, { credentials: 'same-origin' })
  if (!res.ok) throw new Error(`Failed to fetch Slack settings: ${res.status}`)
  return res.json()
}

export async function saveSlackSettings(
  url: string,
  data: SlackSettingsSaveRequest,
): Promise<SlackSettingsPayload> {
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCsrfToken(),
    },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(`Failed to save Slack settings: ${res.status}`)
  return res.json()
}

export async function testSlackConnection(url: string): Promise<SlackTestResult> {
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCsrfToken(),
    },
    body: '{}',
  })
  return res.json()
}
