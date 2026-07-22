import { parseResultObject } from '../../util/objectUtils'

export type AgentConfigField = 'charter' | 'schedule'
export type AgentConfigFieldConfirmation = 'updated' | 'unchanged'

export type AgentConfigUpdateConfirmation = Partial<Record<AgentConfigField, AgentConfigFieldConfirmation>>

function stringSet(value: unknown): Set<string> {
  if (!Array.isArray(value)) {
    return new Set()
  }
  return new Set(value.filter((item): item is string => typeof item === 'string'))
}

export function parseAgentConfigUpdateConfirmation(result: unknown): AgentConfigUpdateConfirmation | null {
  const resultObject = parseResultObject(result)
  if (!resultObject) {
    return null
  }

  const status = typeof resultObject.status === 'string' ? resultObject.status.trim().toLowerCase() : ''
  if (status === 'error' || status === 'failed' || status === 'failure') {
    return null
  }

  const update = parseResultObject(resultObject.agent_config_update)
  if (!update) {
    return null
  }

  const updatedFields = stringSet(update.updated_fields)
  const unchangedFields = stringSet(update.unchanged_fields)
  const errors = parseResultObject(update.errors)
  const confirmation: AgentConfigUpdateConfirmation = {}

  for (const field of ['charter', 'schedule'] as const) {
    if (errors?.[field]) {
      continue
    }
    if (updatedFields.has(field)) {
      confirmation[field] = 'updated'
    } else if (unchangedFields.has(field)) {
      confirmation[field] = 'unchanged'
    }
  }

  return Object.keys(confirmation).length ? confirmation : null
}
