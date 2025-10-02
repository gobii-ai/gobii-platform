export type Attachment = {
  id: string
  filename: string
  url: string
  fileSizeLabel?: string | null
}

export type AgentMessage = {
  id: string
  cursor?: string
  bodyHtml?: string
  bodyText?: string
  isOutbound?: boolean
  channel?: string
  attachments?: Attachment[]
  timestamp?: string | null
  relativeTimestamp?: string | null
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
  caption?: string | null
  timestamp?: string | null
  toolName?: string | null
  showSql?: boolean
  parameters?: unknown
  sqlStatements?: string[]
  result?: string | null
  charterText?: string | null
  cursor?: string
}

export type ToolClusterEvent = {
  kind: 'steps'
  cursor: string
  entryCount: number
  collapsible: boolean
  collapseThreshold: number
  latestTimestamp?: string | null
  earliestTimestamp?: string | null
  entries: ToolCallEntry[]
}

export type ProcessingWebTask = {
  id: string
  status: string
  statusLabel: string
  prompt?: string
  promptPreview: string
  startedAt?: string | null
  updatedAt?: string | null
  elapsedSeconds?: number | null
}

export type ProcessingSnapshot = {
  active: boolean
  webTasks: ProcessingWebTask[]
}

export type MessageEvent = {
  kind: 'message'
  cursor: string
  message: AgentMessage
}

export type TimelineEvent = MessageEvent | ToolClusterEvent

export type AgentTimelineSnapshot = {
  events: TimelineEvent[]
  oldestCursor?: string | null
  newestCursor?: string | null
  hasMoreOlder?: boolean
  hasMoreNewer?: boolean
  processingActive?: boolean
  processingSnapshot?: ProcessingSnapshot
}
