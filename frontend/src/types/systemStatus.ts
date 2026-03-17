export type SystemStatusLevel = 'healthy' | 'warning' | 'critical' | 'info'

export type SystemStatusCard = {
  id: string
  label: string
  value: string | number
  status: SystemStatusLevel
  subtitle: string
}

export type SystemStatusMeta = {
  environment: string
  refreshedAt: string
  pollIntervalSeconds: number
}

export type CeleryStatusSection = {
  available: boolean
  status: SystemStatusLevel
  summary: {
    totalPending: number
    queueCounts: Record<string, number>
  }
  rows: Array<{
    queue: string
    pendingCount: number
  }>
  error?: string
}

export type AgentProcessingStatusSection = {
  available: boolean
  status: SystemStatusLevel
  summary: {
    activeAgentCount: number
    queuedCount: number
    pendingCount: number
    lockedCount: number
    heartbeatCount: number
    queuedOrPendingCount: number
  }
  rows: Array<{
    agentId: string
    agentName: string
    heartbeat: boolean
    queued: boolean
    pending: boolean
    locked: boolean
    stage: string
    lastSeenAt: string
  }>
  error?: string
}

export type WebSessionStatusSection = {
  available: boolean
  status: SystemStatusLevel
  summary: {
    liveCount: number
    ttlSeconds: number
  }
  rows: Array<{
    sessionId: string
    agentId: string
    agentName: string
    userEmail: string
    startedAt: string
    lastSeenAt: string
    lastSeenSource: string
  }>
  error?: string
}

export type ComputeStatusSection = {
  available: boolean
  status: SystemStatusLevel
  summary: {
    runningCount: number
    idleStoppingCount: number
    stoppedCount: number
    errorCount: number
  }
  rows: Array<{
    agentId: string
    agentName: string
    state: string
    namespace: string
    podName: string
    proxyName: string
    lastActivityAt: string
    leaseExpiresAt: string
  }>
  error?: string
}

export type ProxyStatusSection = {
  available: boolean
  status: SystemStatusLevel
  summary: {
    activeCount: number
    healthyCount: number
    degradedCount: number
    staleCount: number
    inactiveCount: number
  }
  rows: Array<{
    proxyId: string
    name: string
    endpoint: string
    classification: string
    isActive: boolean
    latestStatus: string
    latestCheckedAt: string
    responseTimeMs: number | null
    consecutiveHealthFailures: number
    deactivationReason: string
  }>
  error?: string
}

export type BrowserTaskStatusSection = {
  available: boolean
  status: SystemStatusLevel
  summary: {
    pendingCount: number
    inProgressCount: number
    failedCount: number
    activeCount: number
  }
  rows: Array<{
    taskId: string
    agentName: string
    status: string
    createdAt: string
    updatedAt: string
    errorMessage: string
  }>
  error?: string
}

export type SystemStatusPayload = {
  meta: SystemStatusMeta
  overview: SystemStatusCard[]
  sections: {
    celery: CeleryStatusSection
    agents: AgentProcessingStatusSection
    webSessions: WebSessionStatusSection
    compute: ComputeStatusSection
    proxies: ProxyStatusSection
    browserTasks: BrowserTaskStatusSection
  }
}
