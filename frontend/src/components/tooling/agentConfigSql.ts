export type AgentConfigSqlUpdate = {
  updatesCharter: boolean
  updatesSchedule: boolean
  charterValue: string | null
  scheduleValue: string | null
  scheduleCleared: boolean
}

export type SqliteStatementOperation =
  | 'select'
  | 'insert'
  | 'update'
  | 'delete'
  | 'replace'
  | 'create'
  | 'other'

export type SqliteInternalTableKind =
  | 'messages'
  | 'toolResults'
  | 'agentSkills'
  | 'files'

export type SqliteReservedTableKind =
  | 'agentConfig'
  | 'legacyPlan'

export type SqliteStatementClassification = {
  index: number
  statement: string
  operation: SqliteStatementOperation
  tableName: string | null
  internalTableKind: SqliteInternalTableKind | null
  reservedTableKind: SqliteReservedTableKind | null
}

const AGENT_CONFIG_TABLE = '__agent_config'
const MUTATION_RE = /\b(update|insert|replace|delete)\b/i
const SQLITE_INTERNAL_TABLE_NAME_MAP = {
  __messages: 'messages',
  __tool_results: 'toolResults',
  __agent_skills: 'agentSkills',
  __files: 'files',
} satisfies Record<string, SqliteInternalTableKind>

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
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
      if (trimmed.length > 0) {
        parts.push(trimmed)
      }
      current = ''
      continue
    }

    current += char
  }

  const trailing = current.trim()
  if (trailing.length > 0) {
    parts.push(trailing)
  }
  return parts
}

function decodeSqlLiteral(value: string): string | null | undefined {
  const trimmed = value.trim()
  if (!trimmed.length) {
    return undefined
  }
  if (/^null$/i.test(trimmed)) {
    return null
  }
  if (trimmed.startsWith("'") && trimmed.endsWith("'") && trimmed.length >= 2) {
    return trimmed.slice(1, -1).replace(/''/g, "'")
  }
  if (trimmed.startsWith('"') && trimmed.endsWith('"') && trimmed.length >= 2) {
    return trimmed.slice(1, -1).replace(/""/g, '"')
  }
  return undefined
}

function parseInsertValueAssignment(
  statement: string,
  field: string,
): { present: boolean; value: string | null | undefined } {
  const insertMatch = statement.match(
    /\b(?:insert(?:\s+or\s+\w+)?|replace)\b[\s\S]*?\binto\b[\s\S]*?__agent_config\b[\s\S]*?\(([\s\S]*?)\)\s*values\s*\(([\s\S]*?)\)/i,
  )
  if (!insertMatch) {
    return { present: false, value: undefined }
  }

  const rawColumns = insertMatch[1]
  const rawValues = insertMatch[2]
  const columns = splitSqlByComma(rawColumns).map((column) => column.replace(/["'`]/g, '').trim().toLowerCase())
  const values = splitSqlByComma(rawValues)
  if (!columns.length || columns.length !== values.length) {
    return { present: false, value: undefined }
  }

  const fieldLower = field.toLowerCase()
  const targetIndex = columns.findIndex((column) => column === fieldLower)
  if (targetIndex < 0) {
    return { present: false, value: undefined }
  }

  return { present: true, value: decodeSqlLiteral(values[targetIndex] ?? '') }
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

function normalizeSqlForParsing(sql: string): string {
  let output = ''
  let inSingle = false
  let inDouble = false
  let inLineComment = false
  let inBlockComment = false

  for (let idx = 0; idx < sql.length; idx += 1) {
    const char = sql[idx]
    const next = idx + 1 < sql.length ? sql[idx + 1] : ''

    if (inLineComment) {
      output += char === '\n' ? '\n' : ' '
      if (char === '\n') {
        inLineComment = false
      }
      continue
    }

    if (inBlockComment) {
      output += char === '\n' ? '\n' : ' '
      if (char === '*' && next === '/') {
        output += ' '
        idx += 1
        inBlockComment = false
      }
      continue
    }

    if (inSingle) {
      output += ' '
      if (char === "'" && next === "'") {
        output += ' '
        idx += 1
      } else if (char === "'") {
        inSingle = false
      }
      continue
    }

    if (inDouble) {
      output += ' '
      if (char === '"' && next === '"') {
        output += ' '
        idx += 1
      } else if (char === '"') {
        inDouble = false
      }
      continue
    }

    if (char === "'" && !inDouble) {
      inSingle = true
      output += ' '
      continue
    }
    if (char === '"' && !inSingle) {
      inDouble = true
      output += ' '
      continue
    }
    if (char === '-' && next === '-') {
      inLineComment = true
      output += '  '
      idx += 1
      continue
    }
    if (char === '/' && next === '*') {
      inBlockComment = true
      output += '  '
      idx += 1
      continue
    }

    output += char
  }

  return output
}

function extractTopLevelOperation(statement: string): SqliteStatementOperation {
  const normalized = normalizeSqlForParsing(statement)
  let depth = 0
  let token = ''

  const maybeResolveToken = () => {
    if (!token) {
      return null
    }
    const lowered = token.toLowerCase()
    token = ''
    if (depth !== 0) {
      return null
    }
    switch (lowered) {
      case 'select':
      case 'insert':
      case 'update':
      case 'delete':
      case 'replace':
      case 'create':
        return lowered
      default:
        return null
    }
  }

  for (let idx = 0; idx < normalized.length; idx += 1) {
    const char = normalized[idx]
    if (char === '(') {
      const operation = maybeResolveToken()
      if (operation) {
        return operation
      }
      depth += 1
      continue
    }
    if (char === ')') {
      const operation = maybeResolveToken()
      if (operation) {
        return operation
      }
      if (depth > 0) {
        depth -= 1
      }
      continue
    }
    if (/[A-Za-z_]/.test(char)) {
      token += char
      continue
    }

    const operation = maybeResolveToken()
    if (operation) {
      return operation
    }
  }

  return maybeResolveToken() ?? 'other'
}

function cleanTableToken(token: string): string {
  return token.replace(/^[`"'[]+/, '').replace(/[`"'\]]+$/, '').trim().toLowerCase()
}

function collectTableReferences(statement: string, operation: SqliteStatementOperation): string[] {
  const normalized = normalizeSqlForParsing(statement)
  const patterns: RegExp[] = []

  if (operation === 'select' || operation === 'other') {
    patterns.push(/\b(?:from|join)\s+([`"'[]?[A-Za-z_][A-Za-z0-9_.$]*[`"'\]]?)/gi)
  }
  if (operation === 'update' || operation === 'other') {
    patterns.push(/\bupdate\s+([`"'[]?[A-Za-z_][A-Za-z0-9_.$]*[`"'\]]?)/gi)
  }
  if (operation === 'insert' || operation === 'replace' || operation === 'other') {
    patterns.push(/\b(?:insert(?:\s+or\s+\w+)?|replace)\s+into\s+([`"'[]?[A-Za-z_][A-Za-z0-9_.$]*[`"'\]]?)/gi)
  }
  if (operation === 'delete' || operation === 'other') {
    patterns.push(/\bdelete\s+from\s+([`"'[]?[A-Za-z_][A-Za-z0-9_.$]*[`"'\]]?)/gi)
  }
  if (operation === 'create' || operation === 'other') {
    patterns.push(/\bcreate\s+(?:temporary\s+|temp\s+)?table\s+(?:if\s+not\s+exists\s+)?([`"'[]?[A-Za-z_][A-Za-z0-9_.$]*[`"'\]]?)/gi)
  }

  const matches: string[] = []
  for (const pattern of patterns) {
    let match: RegExpExecArray | null
    while ((match = pattern.exec(normalized)) !== null) {
      const tableName = cleanTableToken(match[1] ?? '')
      if (tableName) {
        matches.push(tableName)
      }
    }
  }
  return matches
}

function classifyTableName(tableName: string | null): {
  internalTableKind: SqliteInternalTableKind | null
  reservedTableKind: SqliteReservedTableKind | null
} {
  if (!tableName) {
    return { internalTableKind: null, reservedTableKind: null }
  }
  if (tableName === AGENT_CONFIG_TABLE) {
    return { internalTableKind: null, reservedTableKind: 'agentConfig' }
  }
  if (tableName.startsWith('__kanban')) {
    return { internalTableKind: null, reservedTableKind: 'legacyPlan' }
  }
  return {
    internalTableKind: SQLITE_INTERNAL_TABLE_NAME_MAP[tableName as keyof typeof SQLITE_INTERNAL_TABLE_NAME_MAP] ?? null,
    reservedTableKind: null,
  }
}

export function classifySqliteStatements(statements: string[]): SqliteStatementClassification[] {
  return expandSqlStatements(statements).map((statement, index) => {
    const operation = extractTopLevelOperation(statement)
    const tables = collectTableReferences(statement, operation)
    const uniqueTables = Array.from(new Set(tables))
    const classifications = uniqueTables
      .map((tableName) => ({ tableName, ...classifyTableName(tableName) }))
      .filter((item) => item.internalTableKind !== null || item.reservedTableKind !== null)

    if (classifications.length !== 1) {
      return {
        index,
        statement,
        operation,
        tableName: null,
        internalTableKind: null,
        reservedTableKind: null,
      }
    }

    return {
      index,
      statement,
      operation,
      tableName: classifications[0].tableName,
      internalTableKind: classifications[0].internalTableKind,
      reservedTableKind: classifications[0].reservedTableKind,
    }
  })
}

export function splitSqlStatements(sql: string): string[] {
  const statements: string[] = []
  let current = ''
  let inSingle = false
  let inDouble = false
  let inLineComment = false
  let inBlockComment = false

  for (let idx = 0; idx < sql.length; idx += 1) {
    const char = sql[idx]
    const next = idx + 1 < sql.length ? sql[idx + 1] : ''

    if (inLineComment) {
      current += char
      if (char === '\n') {
        inLineComment = false
      }
      continue
    }

    if (inBlockComment) {
      current += char
      if (char === '*' && next === '/') {
        current += next
        idx += 1
        inBlockComment = false
      }
      continue
    }

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

    if (char === "'" && !inDouble) {
      inSingle = true
      current += char
      continue
    }
    if (char === '"' && !inSingle) {
      inDouble = true
      current += char
      continue
    }
    if (char === '-' && next === '-') {
      inLineComment = true
      current += char
      current += next
      idx += 1
      continue
    }
    if (char === '/' && next === '*') {
      inBlockComment = true
      current += char
      current += next
      idx += 1
      continue
    }

    if (char === ';') {
      const trimmed = current.trim()
      if (trimmed.length > 0) {
        statements.push(trimmed)
      }
      current = ''
      continue
    }

    current += char
  }

  const trailing = current.trim()
  if (trailing.length > 0) {
    statements.push(trailing)
  }
  return statements
}

export function expandSqlStatements(statements: string[]): string[] {
  const expanded: string[] = []
  for (const raw of statements) {
    const value = `${raw ?? ''}`.trim()
    if (!value.length) {
      continue
    }
    const split = splitSqlStatements(value)
    if (split.length) {
      expanded.push(...split)
    } else {
      expanded.push(value)
    }
  }
  return expanded
}

export function parseAgentConfigUpdates(statements: string[]): AgentConfigSqlUpdate | null {
  let updatesCharter = false
  let updatesSchedule = false
  let charterValue: string | null = null
  let scheduleValue: string | null = null
  let scheduleCleared = false

  for (const statement of expandSqlStatements(statements)) {
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
    } else {
      const parsedInsertCharter = parseInsertValueAssignment(statement, 'charter')
      if (parsedInsertCharter.present) {
        updatesCharter = true
        if (parsedInsertCharter.value !== undefined) {
          charterValue = parsedInsertCharter.value
        }
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
    } else {
      const parsedInsertSchedule = parseInsertValueAssignment(statement, 'schedule')
      if (parsedInsertSchedule.present) {
        updatesSchedule = true
        if (parsedInsertSchedule.value === null || parsedInsertSchedule.value === '') {
          scheduleCleared = true
          scheduleValue = null
        } else if (parsedInsertSchedule.value !== undefined) {
          scheduleCleared = false
          scheduleValue = parsedInsertSchedule.value
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
