import type { ReactElement } from 'react'
import type { LucideIcon } from 'lucide-react'
import type { ToolCallEntry } from '../../../types/agentChat'
import type { AgentConfigSqlUpdate, SqliteInternalTableKind, SqliteStatementOperation } from '../../tooling/agentConfigSql'

export type ToolDetailComponent = (props: ToolDetailProps) => ReactElement

export type ToolEntryDisplay = {
  id: string
  clusterCursor: string
  cursor?: string | null
  toolName: string
  label: string
  caption?: string | null
  timestamp?: string | null
  status?: ToolCallEntry['status']
  icon: LucideIcon
  iconSrc?: string | null
  iconBgClass: string
  iconColorClass: string
  parameters: Record<string, unknown> | null
  rawParameters: unknown
  result: unknown
  summary?: string | null
  charterText?: string | null
  agentConfigUpdate?: AgentConfigSqlUpdate | null
  sqlStatements?: string[]
  detailComponent: ToolDetailComponent
  meta?: ToolCallEntry['meta']
  sourceEntry?: ToolCallEntry
  mcpInfo?: {
    serverSlug: string
    serverLabel: string
    toolId: string
    toolLabel: string
  }
  sqliteInfo?: {
    kind: SqliteInternalTableKind
    tableName: string
    operation: SqliteStatementOperation
    operationLabel: string
    purpose: string
    instructionsText?: string | null
    statementIndex: number
  }
  separateFromPreview?: boolean
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

export type ToolDisplayOptions = { timeZone?: string }

export type ToolClusterTransform = ToolClusterDisplay & {
  skippedCount: number
}

export type ToolDescriptorTransform = {
  label?: string
  icon?: LucideIcon
  iconSrc?: string | null
  iconBgClass?: string
  iconColorClass?: string
  caption?: string | null
  charterText?: string | null
  sqlStatements?: string[]
  summary?: string | null
  detailComponent?: ToolDetailComponent
  sqliteInfo?: ToolEntryDisplay['sqliteInfo']
  /** If true, this tool entry should be hidden from the timeline */
  skip?: boolean
  /** If true, this entry should render outside the live preview stream */
  separateFromPreview?: boolean
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
  derive?(entry: ToolCallEntry, parameters: Record<string, unknown> | null, options?: ToolDisplayOptions): ToolDescriptorTransform | void
}
