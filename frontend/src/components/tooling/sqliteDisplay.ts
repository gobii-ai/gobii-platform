import {
  BrainCog,
  FileText,
  MessageSquareText,
  ScanText,
  type LucideIcon,
} from 'lucide-react'

import { parseResultObject } from '../../util/objectUtils'
import type { DetailKind } from '../agentChat/toolDetails'
import type { SqliteInternalTableKind, SqliteStatementOperation } from './agentConfigSql'
import { expandSqlStatements } from './agentConfigSql'

function truncate(value: string, max = 60): string {
  if (value.length <= max) return value
  return `${value.slice(0, max - 1)}…`
}

function coerceString(value: unknown): string | null {
  if (typeof value === 'string' && value.trim().length > 0) {
    return value
  }
  return null
}

export function extractSqlStatementsFromParameters(
  parameters: Record<string, unknown> | null | undefined,
): string[] {
  if (!parameters) {
    return []
  }

  const sqlParam = parameters.sql
  const queryParam = parameters.query
  const queriesParam = parameters.queries
  let rawQueries: unknown[] = []

  if (sqlParam !== undefined && sqlParam !== null) {
    rawQueries = Array.isArray(sqlParam) ? sqlParam : [sqlParam]
  } else if (queryParam !== undefined && queryParam !== null) {
    rawQueries = Array.isArray(queryParam) ? queryParam : [queryParam]
  } else if (queriesParam !== undefined && queriesParam !== null) {
    rawQueries = Array.isArray(queriesParam) ? queriesParam : [queriesParam]
  } else if (Array.isArray(parameters.operations)) {
    rawQueries = parameters.operations
  }

  return expandSqlStatements(rawQueries.map(String))
}

type SqliteInternalTableDescriptor = {
  labelPrefix: string
  tableName: string
  icon: LucideIcon
  iconBgClass: string
  iconColorClass: string
  detailKind: DetailKind
}

const SQLITE_INTERNAL_TABLE_DESCRIPTORS: Record<SqliteInternalTableKind, SqliteInternalTableDescriptor> = {
  messages: {
    labelPrefix: 'Messages',
    tableName: '__messages',
    icon: MessageSquareText,
    iconBgClass: 'bg-blue-100',
    iconColorClass: 'text-blue-700',
    detailKind: 'sqliteInternalTable',
  },
  toolResults: {
    labelPrefix: 'Tool results',
    tableName: '__tool_results',
    icon: ScanText,
    iconBgClass: 'bg-emerald-100',
    iconColorClass: 'text-emerald-700',
    detailKind: 'sqliteInternalTable',
  },
  agentSkills: {
    labelPrefix: 'Skills',
    tableName: '__agent_skills',
    icon: BrainCog,
    iconBgClass: 'bg-amber-100',
    iconColorClass: 'text-amber-700',
    detailKind: 'sqliteInternalTable',
  },
  files: {
    labelPrefix: 'Files',
    tableName: '__files',
    icon: FileText,
    iconBgClass: 'bg-cyan-100',
    iconColorClass: 'text-cyan-700',
    detailKind: 'sqliteInternalTable',
  },
}

function sqliteOperationDisplayLabel(operation: SqliteStatementOperation): string {
  switch (operation) {
    case 'select':
      return 'Query'
    case 'insert':
      return 'Insert'
    case 'update':
      return 'Update'
    case 'delete':
      return 'Delete'
    case 'replace':
      return 'Replace'
    case 'create':
      return 'Create'
    default:
      return 'Change'
  }
}

function sqliteOperationSummaryKind(operation: SqliteStatementOperation): 'query' | 'update' {
  return operation === 'select' ? 'query' : 'update'
}

function splitSqlByComma(value: string): string[] {
  const parts: string[] = []
  let current = ''
  let depth = 0
  let inSingle = false
  let inDouble = false

  for (let idx = 0; idx < value.length; idx += 1) {
    const char = value[idx]
    const next = idx + 1 < value.length ? value[idx + 1] : ''

    if (inSingle) {
      current += char
      if (char === "'" && next === "'") {
        current += next
        idx += 1
      } else if (char === "'") {
        inSingle = false
      }
      continue
    }

    if (inDouble) {
      current += char
      if (char === '"' && next === '"') {
        current += next
        idx += 1
      } else if (char === '"') {
        inDouble = false
      }
      continue
    }

    if (char === "'") {
      inSingle = true
      current += char
      continue
    }
    if (char === '"') {
      inDouble = true
      current += char
      continue
    }
    if (char === '(') {
      depth += 1
      current += char
      continue
    }
    if (char === ')') {
      if (depth > 0) {
        depth -= 1
      }
      current += char
      continue
    }
    if (char === ',' && depth === 0) {
      const trimmed = current.trim()
      if (trimmed) {
        parts.push(trimmed)
      }
      current = ''
      continue
    }

    current += char
  }

  const trailing = current.trim()
  if (trailing) {
    parts.push(trailing)
  }
  return parts
}

function decodeSqlLiteral(value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed.length) {
    return null
  }
  if (trimmed.startsWith("'") && trimmed.endsWith("'") && trimmed.length >= 2) {
    return trimmed.slice(1, -1).replace(/''/g, "'")
  }
  if (trimmed.startsWith('"') && trimmed.endsWith('"') && trimmed.length >= 2) {
    return trimmed.slice(1, -1).replace(/""/g, '"')
  }
  return null
}

function findMatchingParen(value: string, openIndex: number): number {
  let depth = 0
  let inSingle = false
  let inDouble = false

  for (let idx = openIndex; idx < value.length; idx += 1) {
    const char = value[idx]
    const next = idx + 1 < value.length ? value[idx + 1] : ''

    if (inSingle) {
      if (char === "'" && next === "'") {
        idx += 1
      } else if (char === "'") {
        inSingle = false
      }
      continue
    }

    if (inDouble) {
      if (char === '"' && next === '"') {
        idx += 1
      } else if (char === '"') {
        inDouble = false
      }
      continue
    }

    if (char === "'") {
      inSingle = true
      continue
    }
    if (char === '"') {
      inDouble = true
      continue
    }
    if (char === '(') {
      depth += 1
      continue
    }
    if (char === ')') {
      depth -= 1
      if (depth === 0) {
        return idx
      }
    }
  }

  return -1
}

function extractInsertColumnsAndValues(statement: string): { columns: string[]; values: string[] } | null {
  const insertMatch = /\b(?:insert(?:\s+or\s+\w+)?|replace)\b[\s\S]*?\binto\b/i.exec(statement)
  if (!insertMatch) {
    return null
  }

  const insertStart = insertMatch.index + insertMatch[0].length
  const columnsOpenIndex = statement.indexOf('(', insertStart)
  if (columnsOpenIndex < 0) {
    return null
  }
  const columnsCloseIndex = findMatchingParen(statement, columnsOpenIndex)
  if (columnsCloseIndex < 0) {
    return null
  }

  const valuesMatch = /\bvalues\b/i.exec(statement.slice(columnsCloseIndex + 1))
  if (!valuesMatch) {
    return null
  }
  const valuesKeywordIndex = columnsCloseIndex + 1 + valuesMatch.index
  const valuesOpenIndex = statement.indexOf('(', valuesKeywordIndex)
  if (valuesOpenIndex < 0) {
    return null
  }
  const valuesCloseIndex = findMatchingParen(statement, valuesOpenIndex)
  if (valuesCloseIndex < 0) {
    return null
  }

  const columns = splitSqlByComma(statement.slice(columnsOpenIndex + 1, columnsCloseIndex))
    .map((column) => column.replace(/["'`]/g, '').trim().toLowerCase())
  const values = splitSqlByComma(statement.slice(valuesOpenIndex + 1, valuesCloseIndex))
  if (!columns.length || columns.length !== values.length) {
    return null
  }

  return { columns, values }
}

function extractSqlAssignment(statement: string, field: string): string | null {
  const token = field.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
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

function extractInsertValue(statement: string, field: string): string | null {
  const parsed = extractInsertColumnsAndValues(statement)
  if (!parsed) {
    return null
  }
  const { columns, values } = parsed
  const targetIndex = columns.findIndex((column) => column === field.toLowerCase())
  if (targetIndex < 0) {
    return null
  }
  return decodeSqlLiteral(values[targetIndex] ?? '')
}

function extractFieldFromSql(statement: string, fields: string[]): string | null {
  for (const field of fields) {
    const assigned = extractSqlAssignment(statement, field)
    if (assigned) {
      return assigned
    }
    const inserted = extractInsertValue(statement, field)
    if (inserted) {
      return inserted
    }
  }
  return null
}

function extractFieldFromResult(result: unknown, fields: string[]): string | null {
  const resultObject = parseResultObject(result)
  const resultRows = Array.isArray(resultObject?.result) ? resultObject.result : null
  const record = resultRows?.find((row) => row && typeof row === 'object' && !Array.isArray(row)) as Record<string, unknown> | undefined
  if (!record) {
    return null
  }
  for (const field of fields) {
    const value = coerceString(record[field])
    if (value) {
      return value
    }
  }
  return null
}

function extractSqliteTargetLabel(
  kind: SqliteInternalTableKind,
  statement: string,
  result: unknown,
): string | null {
  switch (kind) {
    case 'messages':
      return (
        extractFieldFromSql(statement, ['subject', 'conversation_address', 'from_address', 'to_address', 'channel']) ??
        extractFieldFromResult(result, ['subject', 'conversation_address', 'from_address', 'to_address', 'channel'])
      )
    case 'toolResults':
      return null
    case 'agentSkills':
      return extractFieldFromSql(statement, ['name']) ?? extractFieldFromResult(result, ['name'])
    case 'files':
      return (
        extractFieldFromSql(statement, ['path', 'name', 'parent_path', 'node_id']) ??
        extractFieldFromResult(result, ['path', 'name', 'parent_path', 'node_id'])
      )
    default:
      return null
  }
}

function extractSqliteInstructionsText(statement: string, result: unknown): string | null {
  return extractFieldFromSql(statement, ['instructions']) ?? extractFieldFromResult(result, ['instructions'])
}

export function extractSqliteResultStatus(result: unknown): string | null {
  const resultObject = parseResultObject(result)
  const message = coerceString(resultObject?.message)
  if (message) {
    return truncate(message, 96)
  }
  const status = coerceString(resultObject?.status)
  if (status) {
    return truncate(status, 96)
  }
  return null
}

export function extractSqliteStatementResult(rawResult: unknown, statementIndex: number): unknown {
  const resultObject = parseResultObject(rawResult)
  const results = Array.isArray(resultObject?.results) ? resultObject.results : null
  if (!results?.length) {
    return rawResult
  }
  return results[statementIndex] ?? rawResult
}

export function extractSqliteGroupedResult(rawResult: unknown, statementIndexes: number[]): unknown {
  if (statementIndexes.length === 1) {
    return extractSqliteStatementResult(rawResult, statementIndexes[0])
  }
  const resultObject = parseResultObject(rawResult)
  const results = Array.isArray(resultObject?.results) ? resultObject.results : null
  if (!resultObject || !results?.length) {
    return rawResult
  }
  return {
    ...resultObject,
    results: statementIndexes
      .map((index) => results[index])
      .filter((item) => item !== undefined),
  }
}

export function getSqliteInternalTableDisplay(
  kind: SqliteInternalTableKind,
  operation: SqliteStatementOperation,
  statement: string,
  result?: unknown,
): {
  label: string
  caption: string | null
  summary: string | null
  operationLabel: string
  purpose: string
  instructionsText: string | null
  icon: LucideIcon
  iconBgClass: string
  iconColorClass: string
  detailKind: DetailKind
  tableName: string
} {
  const descriptor = SQLITE_INTERNAL_TABLE_DESCRIPTORS[kind]
  const summaryKind = sqliteOperationSummaryKind(operation)
  const targetLabel = extractSqliteTargetLabel(kind, statement, result)
  const statusSummary = extractSqliteResultStatus(result)
  const instructionsText = kind === 'agentSkills' ? extractSqliteInstructionsText(statement, result) : null

  let purpose = `Interacting with ${descriptor.tableName}.`
  if (kind === 'messages') {
    purpose = summaryKind === 'query' ? 'Querying the agent message snapshot.' : 'Modifying the agent message snapshot.'
  } else if (kind === 'toolResults') {
    purpose = summaryKind === 'query' ? 'Querying stored tool output snapshots.' : 'Modifying stored tool output snapshots.'
  } else if (kind === 'agentSkills') {
    purpose = operation === 'delete'
      ? 'Deleting rows from the agent skills mirror.'
      : summaryKind === 'query'
        ? 'Querying the agent skills mirror.'
        : 'Updating the agent skills mirror.'
  } else if (kind === 'files') {
    purpose = summaryKind === 'query' ? 'Querying the agent file index snapshot.' : 'Modifying the agent file index snapshot.'
  }

  return {
    label: `${descriptor.labelPrefix} ${summaryKind}`,
    caption: kind === 'toolResults'
      ? null
      : kind === 'agentSkills'
        ? (instructionsText ? truncate(instructionsText, 140) : (targetLabel ? truncate(targetLabel, 56) : descriptor.labelPrefix))
        : (targetLabel ? truncate(targetLabel, 56) : descriptor.labelPrefix),
    summary: kind === 'agentSkills' ? null : statusSummary,
    operationLabel: sqliteOperationDisplayLabel(operation),
    purpose,
    instructionsText,
    icon: descriptor.icon,
    iconBgClass: descriptor.iconBgClass,
    iconColorClass: descriptor.iconColorClass,
    detailKind: descriptor.detailKind,
    tableName: descriptor.tableName,
  }
}
