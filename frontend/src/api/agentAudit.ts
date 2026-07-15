import type { AuditEvent, PromptArchive } from '../types/agentAudit'
import { jsonFetch, jsonRequest } from './http'

export type ManualJudgeSuggestion = {
  id: string
  suggestionId: string
  suggestionType: 'intelligence_upgrade' | 'stonewall_reframe' | 'strategy_shift'
  title: string
  message: string
  agentDirective?: string | null
  recommendedTier?: string | null
  status?: string | null
  createdAt?: string | null
  reasoning?: string | null
  completionId?: string | null
  decisionApiUrl?: string | null
}

type ManualJudgeResponse = {
  ran: boolean
  status: string
  suggestion_type?: string | null
  suggestion?: ManualJudgeSuggestion | null
}

export async function fetchPromptArchive(archiveId: string): Promise<PromptArchive> {
  const url = `/console/api/staff/prompt-archives/${archiveId}/`
  return jsonFetch<PromptArchive>(url)
}

export async function triggerProcessEvents(agentId: string): Promise<{ queued: boolean; processing_active: boolean }> {
  const url = `/console/api/staff/agents/${agentId}/developer/process/`
  return jsonRequest(url, { method: 'POST', includeCsrf: true })
}

export async function runAgentJudge(agentId: string): Promise<ManualJudgeResponse> {
  const url = `/console/api/staff/agents/${agentId}/developer/judge/`
  return jsonRequest(url, { method: 'POST', includeCsrf: true })
}

export async function decideAgentJudgeSuggestion(decisionApiUrl: string, decision: 'approve' | 'reject'): Promise<{
  status: string
}> {
  return jsonRequest(decisionApiUrl, { method: 'POST', includeCsrf: true, json: { decision } })
}

export async function createSystemMessage(
  agentId: string,
  payload: { body: string },
): Promise<AuditEvent> {
  const url = `/console/api/staff/agents/${agentId}/system-messages/`
  return jsonRequest(url, { method: 'POST', includeCsrf: true, json: payload })
}

export async function updateSystemMessage(
  agentId: string,
  messageId: string,
  payload: { body?: string },
): Promise<AuditEvent> {
  const url = `/console/api/staff/agents/${agentId}/system-messages/${messageId}/`
  return jsonRequest(url, { method: 'PATCH', includeCsrf: true, json: payload })
}
