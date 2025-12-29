import type { AgentFsNode } from './types'

export function formatBytes(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return '-'
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let current = value
  let idx = 0
  while (current >= 1024 && idx < units.length - 1) {
    current /= 1024
    idx += 1
  }
  return `${current.toFixed(current >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`
}

export function formatTimestamp(iso: string | null): string {
  if (!iso) {
    return '-'
  }
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) {
    return '-'
  }
  return parsed.toLocaleString()
}

export function sortNodes(a: AgentFsNode, b: AgentFsNode): number {
  if (a.nodeType !== b.nodeType) {
    return a.nodeType === 'dir' ? -1 : 1
  }
  return a.name.localeCompare(b.name)
}
