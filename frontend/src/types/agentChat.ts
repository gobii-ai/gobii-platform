import type { SignupPreviewState } from './agentRoster'

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

export type WebhookMeta = {
  contentType?: string | null
  method?: string | null
  path?: string | null
  queryParams?: Record<string, unknown> | null
  payloadKind?: string | null
  payload?: unknown
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
  senderUserId?: number | null
  senderName?: string | null
  senderAddress?: string | null
  sourceKind?: string | null
  sourceLabel?: string | null
  webhookMeta?: WebhookMeta | null
}

export type ToolMeta = {
  label: string
  iconPaths: string[]
  iconBg: string
  iconColor: string
}

export type ToolCallStatus = 'pending' | 'complete' | 'error'

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
  status?: ToolCallStatus | null
  cursor?: string
  chartImageUrl?: string | null
  createImageUrl?: string | null
  createVideoUrl?: string | null
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
  planEntries?: PlanEvent[]
  visibleDisplayEntryIds?: string[]
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
  nextScheduledAt?: string | null
}

export type AgentMessageNotificationWorkspace = {
  type: 'personal' | 'organization'
  id: string
}

export type AgentMessageNotification = {
  agent_id: string
  agent_name: string
  agent_avatar_url: string | null
  workspace: AgentMessageNotificationWorkspace
  has_unread_agent_message?: boolean
  latest_agent_message_id?: string | null
  latest_agent_message_at?: string | null
  latest_agent_message_read_at?: string | null
  message: {
    id: string
    body_preview: string
    timestamp: string | null
    channel: string
  }
}

export type HumanInputOption = {
  key: string
  title: string
  description: string
}

export type PendingHumanInputRequestStatus = 'pending' | 'answered' | 'cancelled' | 'expired'

export type PendingHumanInputRequestInputMode = 'options_plus_text' | 'free_text_only'

export type PendingHumanInputRequest = {
  id: string
  question: string
  options: HumanInputOption[]
  createdAt?: string | null
  status: PendingHumanInputRequestStatus
  activeConversationChannel?: string | null
  inputMode: PendingHumanInputRequestInputMode
  batchId: string
  batchPosition: number
  batchSize: number
}

export type PendingSpawnRequest = {
  id: string
  requestId: string
  requestedCharter: string
  handoffMessage?: string | null
  requestReason?: string | null
  requestedAt?: string | null
  expiresAt?: string | null
  decisionApiUrl?: string | null
}

export type RequestedSecret = {
  id: string
  name: string
  key: string
  secretType: 'credential' | 'env_var'
  domainPattern: string
  description?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

export type PendingRequestedSecretsAction = {
  id: string
  kind: 'requested_secrets'
  secrets: RequestedSecret[]
  count: number
  fulfillApiUrl?: string | null
  removeApiUrl?: string | null
}

export type PendingContactRequest = {
  id: string
  channel: string
  address: string
  name?: string | null
  reason?: string | null
  purpose?: string | null
  allowInbound: boolean
  allowOutbound: boolean
  canConfigure: boolean
  requestedAt?: string | null
  expiresAt?: string | null
}

export type PendingContactRequestsAction = {
  id: string
  kind: 'contact_requests'
  requests: PendingContactRequest[]
  count: number
  resolveApiUrl?: string | null
}

export type PendingHumanInputAction = {
  id: string
  kind: 'human_input'
  requests: PendingHumanInputRequest[]
  count: number
}

export type PendingSpawnRequestAction = {
  id: string
  kind: 'spawn_request'
} & PendingSpawnRequest

export type PendingActionRequest =
  | PendingHumanInputAction
  | PendingSpawnRequestAction
  | PendingRequestedSecretsAction
  | PendingContactRequestsAction

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

export type PlanStepChange = {
  stepId: string
  cardId?: string
  title: string
  action: 'created' | 'started' | 'completed' | 'updated' | 'deleted' | 'archived'
  fromStatus?: string | null
  toStatus?: string | null
}

export type PlanFileDeliverable = {
  path: string
  label?: string | null
  downloadUrl?: string | null
}

export type PlanMessageDeliverable = {
  messageId: string
  label?: string | null
}

export type PlanSnapshot = {
  todoCount: number
  doingCount: number
  doneCount: number
  todoTitles: string[]
  doingTitles: string[]
  doneTitles: string[]
  files?: PlanFileDeliverable[]
  messages?: PlanMessageDeliverable[]
}

export type PlanEvent = {
  kind: 'plan'
  cursor: string
  timestamp?: string | null
  agentName: string
  displayText: string
  primaryAction: 'created' | 'started' | 'completed' | 'updated' | 'deleted' | 'archived'
  changes: PlanStepChange[]
  snapshot: PlanSnapshot
}

export type HistoricalPlanCompatEvent = Omit<PlanEvent, 'kind'> & {
  kind: 'kanban'
  changes: Array<Omit<PlanStepChange, 'stepId'> & { cardId: string; stepId?: string }>
}

export type TimelineEvent = MessageEvent | ToolClusterEvent | ThinkingEvent | PlanEvent | HistoricalPlanCompatEvent

export type AgentTimelineSnapshot = {
  events: TimelineEvent[]
  oldestCursor?: string | null
  newestCursor?: string | null
  hasMoreOlder?: boolean
  hasMoreNewer?: boolean
  processingActive?: boolean
  processingSnapshot?: ProcessingSnapshot
  signupPreviewState?: SignupPreviewState | null
  pendingHumanInputRequests?: PendingHumanInputRequest[]
  pendingActionRequests?: PendingActionRequest[]
  currentPlan?: PlanSnapshot | null
}

export type StreamEventPayload = {
  stream_id: string
  status: 'start' | 'delta' | 'done' | 'canceled'
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
