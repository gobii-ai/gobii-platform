export type Attachment = {
  id: string
  filename: string
  url: string
  downloadUrl?: string | null
  filespacePath?: string | null
  filespaceNodeId?: string | null
  fileSizeLabel?: string | null
}

export type PeerAgentRef = {
  id: string
  name?: string | null
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
  clientId?: string
  status?: 'sending' | 'failed'
  error?: string | null
  isPeer?: boolean
  peerAgent?: PeerAgentRef | null
  peerLinkId?: string | null
  selfAgentName?: string | null
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
  thinkingEntries?: ThinkingEvent[]
  kanbanEntries?: KanbanEvent[]
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

export type ThinkingEvent = {
  kind: 'thinking'
  cursor: string
  timestamp?: string | null
  reasoning: string
  completionId?: string | null
}

export type KanbanCardChange = {
  cardId: string
  title: string
  action: 'created' | 'started' | 'completed' | 'updated' | 'deleted' | 'archived'
  fromStatus?: string | null
  toStatus?: string | null
}

export type KanbanBoardSnapshot = {
  todoCount: number
  doingCount: number
  doneCount: number
  todoTitles: string[]
  doingTitles: string[]
  doneTitles: string[]
}

export type KanbanEvent = {
  kind: 'kanban'
  cursor: string
  timestamp?: string | null
  agentName: string
  displayText: string
  primaryAction: 'created' | 'started' | 'completed' | 'updated' | 'deleted' | 'archived'
  changes: KanbanCardChange[]
  snapshot: KanbanBoardSnapshot
}

export type TimelineEvent = MessageEvent | ToolClusterEvent | ThinkingEvent | KanbanEvent

export type AgentTimelineSnapshot = {
  events: TimelineEvent[]
  oldestCursor?: string | null
  newestCursor?: string | null
  hasMoreOlder?: boolean
  hasMoreNewer?: boolean
  processingActive?: boolean
  processingSnapshot?: ProcessingSnapshot
}

export type StreamEventPayload = {
  stream_id: string
  status: 'start' | 'delta' | 'done'
  reasoning_delta?: string | null
  content_delta?: string | null
  timestamp?: string | null
}

export type StreamState = {
  streamId: string
  reasoning: string
  content: string
  done: boolean
  cursor?: string | null
  source?: 'stream' | 'timeline'
}
