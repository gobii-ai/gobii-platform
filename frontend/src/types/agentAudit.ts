export type TokenTotals = {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cached_tokens: number
}

export type PromptArchiveMeta = {
  id: string
  rendered_at: string | null
  tokens_before: number
  tokens_after: number
  tokens_saved: number
}

export type AuditToolCallEvent = {
  kind: 'tool_call'
  id: string
  timestamp: string | null
  completion_id: string | null
  tool_name: string | null
  parameters: unknown
  result: string | null
  prompt_archive?: PromptArchiveMeta | null
}

export type AuditCompletionEvent = {
  kind: 'completion'
  id: string
  timestamp: string | null
  completion_type: string
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  cached_tokens: number | null
  llm_model: string | null
  llm_provider: string | null
  thinking?: string | null
  prompt_archive?: PromptArchiveMeta | null
  tool_calls?: AuditToolCallEvent[]
}

export type AuditMessageEvent = {
  kind: 'message'
  id: string
  timestamp: string | null
  is_outbound: boolean
  channel: string | null
  body_html: string | null
  body_text: string | null
  attachments: {
    id: string
    filename: string
    url: string
    download_url?: string | null
    filespace_path?: string | null
    filespace_node_id?: string | null
    file_size_label?: string | null
  }[]
  peer_agent?: { id: string; name?: string | null } | null
  peer_link_id?: string | null
  self_agent_name?: string | null
}

export type AuditStepEvent = {
  kind: 'step'
  id: string
  timestamp: string | null
  description: string
  completion_id: string | null
  is_system: boolean
  system_code?: string | null
  system_notes?: string | null
}

export type AuditSystemMessageEvent = {
  kind: 'system_message'
  id: string
  timestamp: string | null
  delivered_at: string | null
  body: string
  is_active: boolean
  broadcast_id: string | null
  created_by: {
    id: string
    email?: string | null
    name?: string | null
  } | null
}

export type AuditEvent = AuditCompletionEvent | AuditToolCallEvent | AuditMessageEvent | AuditStepEvent | AuditRunStartedEvent | AuditSystemMessageEvent
export type AuditRunStartedEvent = {
  kind: 'run_started'
  run_id: string
  timestamp: string | null
  sequence: number | null
}

export type PromptArchive = {
  id: string
  agent_id: string
  rendered_at: string
  tokens_before: number
  tokens_after: number
  tokens_saved: number
  payload: {
    system_prompt?: string
    user_prompt?: string
    [key: string]: unknown
  } | null
}

export type AuditTimelineBucket = {
  day: string // YYYY-MM-DD in local timezone
  count: number
}
