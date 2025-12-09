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
    file_size_label?: string | null
  }[]
  peer_agent?: { id: string; name?: string | null } | null
  peer_link_id?: string | null
  self_agent_name?: string | null
}

export type AuditEvent = AuditCompletionEvent | AuditToolCallEvent | AuditMessageEvent | AuditRunStartedEvent

export type AuditRunStartedEvent = {
  kind: 'run_started'
  run_id: string
  timestamp: string | null
  sequence: number | null
}

export type AuditRun = {
  run_id: string
  sequence: number
  started_at: string
  ended_at: string | null
  events: AuditEvent[]
  token_totals: TokenTotals
  active?: boolean
  collapsed?: boolean
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
