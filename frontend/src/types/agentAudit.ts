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
  result: unknown
  execution_duration_ms?: number | null
  prompt_archive?: PromptArchiveMeta | null
}

export type AuditCompletionEvent = {
  kind: 'completion'
  id: string
  timestamp: string | null
  completion_type: string
  response_id: string | null
  request_duration_ms: number | null
  time_to_first_token_ms: number | null
  completion_tokens_per_second: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  cached_tokens: number | null
  llm_model: string | null
  llm_provider: string | null
  llm_tool_names?: string[]
  thinking?: string | null
  prompt_archive?: PromptArchiveMeta | null
  tool_calls?: AuditToolCallEvent[]
}

export type AuditErrorEvent = {
  kind: 'error'
  id: string
  timestamp: string | null
  category: string
  source: string
  level: string
  message: string
  exception_class: string
  traceback: string
  context: Record<string, unknown>
  completion_id: string | null
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
  broadcast_id: string | null
  created_by: {
    id: string
    email?: string | null
    name?: string | null
  } | null
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
