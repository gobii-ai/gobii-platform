import type { AgentRosterEntry, AgentRosterSortMode } from '../types/agentRoster'

function compareRosterNames(left: string, right: string): number {
  return left.localeCompare(right, undefined, { sensitivity: 'base' })
}

function compareRosterIds(leftId: string, rightId: string): number {
  return leftId.localeCompare(rightId, undefined, { sensitivity: 'base' })
}

function parseLastInteractionTimestamp(value: string | null | undefined): number {
  if (typeof value !== 'string' || !value.trim()) {
    return Number.NEGATIVE_INFINITY
  }
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY
}

function compareRosterEntries(
  left: AgentRosterEntry,
  right: AgentRosterEntry,
  sortMode: AgentRosterSortMode,
): number {
  if (sortMode === 'recent') {
    const leftTimestamp = parseLastInteractionTimestamp(left.lastInteractionAt)
    const rightTimestamp = parseLastInteractionTimestamp(right.lastInteractionAt)
    if (rightTimestamp !== leftTimestamp) {
      return rightTimestamp - leftTimestamp
    }
  }

  const nameComparison = compareRosterNames(left.name || '', right.name || '')
  if (nameComparison !== 0) {
    return nameComparison
  }
  return compareRosterIds(left.id, right.id)
}

export function sortRosterEntries(agents: AgentRosterEntry[], sortMode: AgentRosterSortMode): AgentRosterEntry[] {
  return [...agents].sort((left, right) => compareRosterEntries(left, right, sortMode))
}

export function parseAgentRosterSortMode(value: unknown): AgentRosterSortMode {
  return value === 'alphabetical' ? 'alphabetical' : 'recent'
}
