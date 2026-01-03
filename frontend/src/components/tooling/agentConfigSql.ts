export type AgentConfigSqlUpdate = {
  updatesCharter: boolean
  updatesSchedule: boolean
  charterValue: string | null
  scheduleValue: string | null
  scheduleCleared: boolean
}

const AGENT_CONFIG_TABLE = '__agent_config'
const MUTATION_RE = /\b(update|insert|replace|delete)\b/i

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function extractSqlAssignment(statement: string, field: string): string | null {
  const token = escapeRegExp(field)
  const singleQuote = new RegExp(`\\b${token}\\b\\s*=\\s*'((?:[^']|'')*)'`, 'i')
  const singleMatch = statement.match(singleQuote)
  if (singleMatch) {
    return singleMatch[1].replace(/''/g, "'")
  }
  const doubleQuote = new RegExp(`\\b${token}\\b\\s*=\\s*"((?:[^"]|"")*)"`, 'i')
  const doubleMatch = statement.match(doubleQuote)
  if (doubleMatch) {
    return doubleMatch[1].replace(/""/g, '"')
  }
  return null
}

function hasAssignment(statement: string, field: string): boolean {
  const token = escapeRegExp(field)
  const assignRe = new RegExp(`\\b${token}\\b\\s*=`, 'i')
  return assignRe.test(statement)
}

function isClearingAssignment(statement: string, field: string): boolean {
  const token = escapeRegExp(field)
  const nullRe = new RegExp(`\\b${token}\\b\\s*=\\s*null\\b`, 'i')
  const emptySingleRe = new RegExp(`\\b${token}\\b\\s*=\\s*''`, 'i')
  const emptyDoubleRe = new RegExp(`\\b${token}\\b\\s*=\\s*""`, 'i')
  return nullRe.test(statement) || emptySingleRe.test(statement) || emptyDoubleRe.test(statement)
}

export function parseAgentConfigUpdates(statements: string[]): AgentConfigSqlUpdate | null {
  let updatesCharter = false
  let updatesSchedule = false
  let charterValue: string | null = null
  let scheduleValue: string | null = null
  let scheduleCleared = false

  for (const statement of statements) {
    const normalized = statement.toLowerCase()
    if (!normalized.includes(AGENT_CONFIG_TABLE)) {
      continue
    }
    if (!MUTATION_RE.test(statement)) {
      continue
    }

    if (hasAssignment(statement, 'charter')) {
      updatesCharter = true
      const parsedCharter = extractSqlAssignment(statement, 'charter')
      if (parsedCharter !== null) {
        charterValue = parsedCharter
      }
    }

    if (hasAssignment(statement, 'schedule')) {
      updatesSchedule = true
      if (isClearingAssignment(statement, 'schedule')) {
        scheduleCleared = true
        scheduleValue = null
      } else {
        const parsedSchedule = extractSqlAssignment(statement, 'schedule')
        if (parsedSchedule !== null) {
          scheduleValue = parsedSchedule
          scheduleCleared = false
        }
      }
    }
  }

  if (!updatesCharter && !updatesSchedule) {
    return null
  }

  return {
    updatesCharter,
    updatesSchedule,
    charterValue,
    scheduleValue,
    scheduleCleared,
  }
}
