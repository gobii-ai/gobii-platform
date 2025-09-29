export type Attachment = {
  id: string
  filename: string
  url: string
  fileSizeLabel?: string
}

export type AgentMessage = {
  id: string
  cursor?: string
  bodyHtml?: string
  bodyText?: string
  isOutbound?: boolean
  channel?: string
  attachments?: Attachment[]
  timestamp?: string
  relativeTimestamp?: string
}

export type ToolMeta = {
  label: string
  iconPaths: string[]
  iconBg: string
  iconColor: string
}

export type ToolCallEntry = {
  id: string
  meta: ToolMeta
  summary?: string
  caption?: string
  timestamp?: string
  toolName?: string
  showSql?: boolean
  parameters?: unknown
  sqlStatements?: string[]
  result?: string | null
  charterText?: string | null
}

export type ToolClusterEvent = {
  kind: 'steps'
  cursor: string
  entryCount: number
  collapsible: boolean
  collapseThreshold: number
  latestTimestamp?: string
  earliestTimestamp?: string
  entries: ToolCallEntry[]
}

export type MessageEvent = {
  kind: 'message'
  cursor: string
  message: AgentMessage
}

export type TimelineEvent = MessageEvent | ToolClusterEvent

export type AgentTimelineProps = {
  agentFirstName: string
  events: TimelineEvent[]
  hasMoreOlder?: boolean
  hasMoreNewer?: boolean
  oldestCursor?: string | null
  newestCursor?: string | null
  processingActive?: boolean
}
