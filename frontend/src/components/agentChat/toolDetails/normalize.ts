import { isRecord } from '../../../util/objectUtils'

export type NormalizeContext = {
  depth: number
  maxDepth: number
  seen: WeakSet<object>
}

export function tryParseJson(content: string): unknown | null {
  const trimmed = content.trim()
  if (!trimmed) return null
  if (trimmed.length < 2) return null
  const firstChar = trimmed[0]
  if (!['{', '['].includes(firstChar)) {
    return null
  }
  const expectedClosing = firstChar === '{' ? '}' : ']'
  if (!trimmed.endsWith(expectedClosing)) {
    return null
  }
  try {
    return JSON.parse(trimmed)
  } catch {
    return null
  }
}

export function createNormalizeContext(maxDepth = 6): NormalizeContext {
  return {
    depth: 0,
    maxDepth,
    seen: new WeakSet<object>(),
  }
}

export function normalizeStructuredValue(value: unknown, context: NormalizeContext): unknown {
  if (value === null || value === undefined) {
    return value
  }

  if (typeof value === 'string') {
    if (context.depth >= context.maxDepth) {
      return value
    }
    const parsed = tryParseJson(value)
    if (parsed !== null) {
      return normalizeStructuredValue(parsed, { ...context, depth: context.depth + 1 })
    }
    return value
  }

  if (Array.isArray(value)) {
    if (context.seen.has(value)) {
      return value
    }
    context.seen.add(value)
    if (context.depth >= context.maxDepth) {
      return value
    }
    const nextDepth = context.depth + 1
    let mutated = false
    const normalized = value.map((item) => {
      const normalizedItem = normalizeStructuredValue(item, { ...context, depth: nextDepth })
      if (normalizedItem !== item) {
        mutated = true
      }
      return normalizedItem
    })
    return mutated ? normalized : value
  }

  if (isRecord(value)) {
    if (context.seen.has(value)) {
      return value
    }
    context.seen.add(value)
    if (context.depth >= context.maxDepth) {
      return value
    }
    const nextDepth = context.depth + 1
    let mutated = false
    const entries = Object.entries(value)
    const normalizedEntries: Array<[string, unknown]> = entries.map(([key, child]) => {
      const normalizedChild = normalizeStructuredValue(child, { ...context, depth: nextDepth })
      if (normalizedChild !== child) {
        mutated = true
      }
      return [key, normalizedChild]
    })
    if (!mutated) {
      return value
    }
    const normalizedObject: Record<string, unknown> = {}
    for (const [key, child] of normalizedEntries) {
      normalizedObject[key] = child
    }
    return normalizedObject
  }

  return value
}
