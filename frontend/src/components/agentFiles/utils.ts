import type { AgentFsNode } from './types'
import { formatBytes as formatByteSize } from '../../util/formatBytes'

export function formatBytes(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return '-'
  }
  return formatByteSize(value)
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
