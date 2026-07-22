export type AgentConfigCharterChange = {
  previousText: string | null
  replacementText: string | null
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
const SQLITE_INTERNAL_TABLE_NAME_MAP = {
  __messages: 'messages',
  __tool_results: 'toolResults',
  __agent_skills: 'agentSkills',
  __files: 'files',
} satisfies Record<string, SqliteInternalTableKind>

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

function normalizeEscapedNewlinesForDisplay(value: string | null): string | null {
  return value?.replace(/\\r\\n|\\n|\\r/g, '\n') ?? null
}

function extractUpdateAssignments(statement: string): string | null {
  const normalized = normalizeSqlForParsing(statement)
  const update = /\bupdate\s+[`"\[]?__agent_config[`"\]]?\s+[\s\S]*?\bset\b/i.exec(normalized)
  if (!update) return null
  const assignmentsStart = update.index + update[0].length
  const assignmentsTail = normalized.slice(assignmentsStart)
  const terminator = /\b(?:where|returning)\b/i.exec(assignmentsTail)
  const assignmentsEnd = assignmentsStart + (terminator?.index ?? assignmentsTail.length)
  return statement.slice(assignmentsStart, assignmentsEnd)
}

function parsePatchTextAssignment(statement: string): AgentConfigCharterChange | null {
  const match = statement.match(
    /\bcharter\b\s*=\s*patch_text\s*\(\s*([`"'\[\]A-Z_][`"'\[\]A-Z0-9_.]*)\s*,\s*(null|'(?:[^']|'')*'|"(?:[^"]|"")*")\s*,\s*(null|'(?:[^']|'')*'|"(?:[^"]|"")*")\s*\)/i,
  )
  if (!match) {
    return null
  }

  const target = match[1].replace(/[`"'\[\]]/g, '').trim().toLowerCase()
  if (target !== 'charter' && !target.endsWith('.charter')) {
    return null
  }

  const decodedPreviousText = decodeSqlLiteral(match[2] ?? '')
  const decodedReplacementText = decodeSqlLiteral(match[3] ?? '')
  if (decodedPreviousText === undefined || decodedReplacementText === undefined) {
    return null
  }
  return {
    previousText: normalizeEscapedNewlinesForDisplay(decodedPreviousText),
    replacementText: normalizeEscapedNewlinesForDisplay(decodedReplacementText),
  }
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

export function parseAgentConfigCharterChange(statements: string[]): AgentConfigCharterChange | null {
  let charterChange: AgentConfigCharterChange | null = null
  for (const statement of expandSqlStatements(statements)) {
    const updateAssignments = extractUpdateAssignments(statement)
    const parsedChange = updateAssignments && parsePatchTextAssignment(updateAssignments)
    if (parsedChange) {
      charterChange = parsedChange
    }
  }
  return charterChange
}
