export function splitSqlByComma(value: string): string[] {
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
