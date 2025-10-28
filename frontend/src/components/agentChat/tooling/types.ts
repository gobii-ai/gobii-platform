import type { ReactElement } from 'react'
import type { LucideIcon } from 'lucide-react'
import type { ToolCallEntry, ToolClusterEvent } from '../../../types/agentChat'

export type ToolDetailComponent = (props: ToolDetailProps) => ReactElement

export type ToolEntryDisplay = {
  id: string
  clusterCursor: string
  cursor?: string | null
  toolName: string
  label: string
  caption?: string | null
  timestamp?: string | null
  icon: LucideIcon
  iconBgClass: string
  iconColorClass: string
  parameters: Record<string, unknown> | null
  rawParameters: unknown
  result: unknown
  summary?: string | null
  charterText?: string | null
  sqlStatements?: string[]
  detailComponent: ToolDetailComponent
  meta?: ToolCallEntry['meta']
  sourceEntry: ToolCallEntry
}

export type ToolClusterDisplay = {
  cursor: string
  entryCount: number
  collapseThreshold: number
  collapsible: boolean
  entries: ToolEntryDisplay[]
  latestTimestamp?: string | null
  earliestTimestamp?: string | null
}

export type ToolDetailProps = {
  entry: ToolEntryDisplay
}

export type ToolClusterTransform = ToolClusterDisplay & {
  skippedCount: number
}

export type ToolDescriptorTransform = {
  label?: string
  icon?: LucideIcon
  iconBgClass?: string
  iconColorClass?: string
  caption?: string | null
  charterText?: string | null
  sqlStatements?: string[]
  summary?: string | null
  detailComponent?: ToolDetailComponent
}

export type ToolDescriptor = {
  name: string
  aliases?: string[]
  label: string
  icon: LucideIcon
  iconBgClass: string
  iconColorClass: string
  detailComponent: ToolDetailComponent
  skip?: boolean
  derive?(entry: ToolCallEntry, parameters: Record<string, unknown> | null): ToolDescriptorTransform | void
}

export type ClusterTransformOptions = {
  skipTools?: Set<string>
}

export type ToolClusterTransformFn = (
  cluster: ToolClusterEvent,
) => ToolClusterTransform

export type ToolEntryKey = {
  clusterCursor: string
  entryId: string
}
