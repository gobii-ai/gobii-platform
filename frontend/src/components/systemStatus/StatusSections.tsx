import type { AgentErrorStatusSection, AgentProcessingStatusSection, BrowserTaskStatusSection, CeleryStatusSection, ComputeStatusSection, ProxyStatusSection, SystemStatusPayload, WebSessionStatusSection } from '../../types/systemStatus'
import { BooleanPill, DataTable, EmptyRows, SectionCard, UnavailableSection, formatDateTime, formatStatusLabel } from './common'

function CelerySection({ section }: { section: CeleryStatusSection }) {
  return (
    <SectionCard
      title="Celery Backlog"
      status={section.status}
      summary={[
        { label: 'Total', value: section.summary.totalPending },
        { label: 'Queues', value: Object.keys(section.summary.queueCounts).length },
      ]}
    >
      {section.rows.length ? (
        <DataTable
          columns={[
            { key: 'queue', label: 'Queue', render: (row) => row.queue },
            { key: 'pendingCount', label: 'Pending', align: 'right', render: (row) => row.pendingCount },
          ]}
          rows={section.rows}
          getRowKey={(row) => row.queue}
        />
      ) : (
        <EmptyRows />
      )}
    </SectionCard>
  )
}

function AgentSection({ section }: { section: AgentProcessingStatusSection }) {
  return (
    <SectionCard
      title="Agent Processing"
      status={section.status}
      summary={[
        { label: 'Active', value: section.summary.activeAgentCount },
        { label: 'Queued', value: section.summary.queuedCount },
        { label: 'Pending', value: section.summary.pendingCount },
        { label: 'Locked', value: section.summary.lockedCount },
      ]}
    >
      {section.rows.length ? (
        <DataTable
          columns={[
            { key: 'agentName', label: 'Agent', render: (row) => row.agentName },
            { key: 'stage', label: 'Stage', render: (row) => row.stage || '—' },
            {
              key: 'flags',
              label: 'Flags',
              render: (row) => (
                <div className="flex flex-wrap gap-1.5">
                  <BooleanPill active={row.heartbeat} label="Heartbeat" />
                  <BooleanPill active={row.queued} label="Queued" />
                  <BooleanPill active={row.pending} label="Pending" />
                  <BooleanPill active={row.locked} label="Locked" />
                </div>
              ),
            },
            { key: 'lastSeenAt', label: 'Last Seen', render: (row) => formatDateTime(row.lastSeenAt) },
          ]}
          rows={section.rows}
          getRowKey={(row) => row.agentId}
        />
      ) : (
        <EmptyRows />
      )}
    </SectionCard>
  )
}

function WebSessionSection({ section }: { section: WebSessionStatusSection }) {
  return (
    <SectionCard
      title="Active Web Sessions"
      status={section.status}
      summary={[
        { label: 'Live', value: section.summary.liveCount },
        { label: 'TTL', value: `${section.summary.ttlSeconds}s` },
      ]}
    >
      {section.rows.length ? (
        <DataTable
          columns={[
            { key: 'agentName', label: 'Agent', render: (row) => row.agentName },
            { key: 'userEmail', label: 'User', render: (row) => row.userEmail },
            { key: 'lastSeenSource', label: 'Source', render: (row) => row.lastSeenSource || '—' },
            { key: 'lastSeenAt', label: 'Last Seen', render: (row) => formatDateTime(row.lastSeenAt) },
          ]}
          rows={section.rows}
          getRowKey={(row) => row.sessionId}
        />
      ) : (
        <EmptyRows />
      )}
    </SectionCard>
  )
}

function ComputeSection({ section }: { section: ComputeStatusSection }) {
  return (
    <SectionCard
      title="Sandbox Compute"
      status={section.status}
      summary={[
        { label: 'Running', value: section.summary.runningCount },
        { label: 'Idle Stopping', value: section.summary.idleStoppingCount },
        { label: 'Errors', value: section.summary.errorCount },
      ]}
    >
      {section.rows.length ? (
        <DataTable
          columns={[
            { key: 'agentName', label: 'Agent', render: (row) => row.agentName },
            { key: 'state', label: 'State', render: (row) => formatStatusLabel(row.state) },
            { key: 'podName', label: 'Pod', render: (row) => row.podName || '—' },
            { key: 'leaseExpiresAt', label: 'Lease', render: (row) => formatDateTime(row.leaseExpiresAt) },
          ]}
          rows={section.rows}
          getRowKey={(row) => row.agentId}
        />
      ) : (
        <EmptyRows />
      )}
    </SectionCard>
  )
}

function ProxySection({ section }: { section: ProxyStatusSection }) {
  return (
    <SectionCard
      title="Proxy Health"
      status={section.status}
      summary={[
        { label: 'Active', value: section.summary.activeCount },
        { label: 'Healthy', value: section.summary.healthyCount },
        { label: 'Stale', value: section.summary.staleCount },
        { label: 'Degraded', value: section.summary.degradedCount },
      ]}
    >
      {section.rows.length ? (
        <DataTable
          columns={[
            { key: 'name', label: 'Proxy', render: (row) => row.name },
            { key: 'endpoint', label: 'Endpoint', render: (row) => row.endpoint },
            { key: 'classification', label: 'Class', render: (row) => formatStatusLabel(row.classification) },
            { key: 'latestStatus', label: 'Latest Check', render: (row) => row.latestStatus || '—' },
            { key: 'latestCheckedAt', label: 'Checked', render: (row) => formatDateTime(row.latestCheckedAt) },
          ]}
          rows={section.rows}
          getRowKey={(row) => row.proxyId}
        />
      ) : (
        <EmptyRows />
      )}
    </SectionCard>
  )
}

function BrowserTaskSection({ section }: { section: BrowserTaskStatusSection }) {
  return (
    <SectionCard
      title="Browser Tasks"
      status={section.status}
      summary={[
        { label: 'Pending', value: section.summary.pendingCount },
        { label: 'In Progress', value: section.summary.inProgressCount },
        { label: 'Failed', value: section.summary.failedCount },
      ]}
    >
      {section.rows.length ? (
        <DataTable
          columns={[
            { key: 'agentName', label: 'Agent', render: (row) => row.agentName || '—' },
            { key: 'status', label: 'Status', render: (row) => formatStatusLabel(row.status) },
            { key: 'updatedAt', label: 'Updated', render: (row) => formatDateTime(row.updatedAt) },
            { key: 'errorMessage', label: 'Error', render: (row) => row.errorMessage || '—' },
          ]}
          rows={section.rows}
          getRowKey={(row) => row.taskId}
        />
      ) : (
        <EmptyRows />
      )}
    </SectionCard>
  )
}

function AgentErrorsSection({ section }: { section: AgentErrorStatusSection }) {
  return (
    <SectionCard
      title="Agent Errors"
      status={section.status}
      summary={[
        { label: 'Total', value: section.summary.totalCount },
        { label: 'Affected Agents', value: section.summary.affectedAgentCount },
        { label: 'Signatures', value: section.summary.signatureCount },
        { label: 'Window', value: `${section.summary.windowMinutes}m` },
      ]}
    >
      {section.rows.length ? (
        <DataTable
          columns={[
            { key: 'category', label: 'Category', render: (row) => formatStatusLabel(row.category) },
            { key: 'source', label: 'Source', render: (row) => row.source || '—' },
            { key: 'exceptionClass', label: 'Exception', render: (row) => row.exceptionClass || '—' },
            { key: 'count', label: 'Count', align: 'right', render: (row) => row.count },
            { key: 'latestAt', label: 'Latest', render: (row) => formatDateTime(row.latestAt) },
            {
              key: 'message',
              label: 'Message',
              render: (row) => (
                <div className="max-w-xl">
                  <div className="text-slate-700">{row.message || '—'}</div>
                  {row.sampleAgentNames.length ? (
                    <div className="mt-1 text-xs text-slate-500">
                      {row.affectedAgentCount} agents: {row.sampleAgentNames.join(', ')}
                    </div>
                  ) : null}
                </div>
              ),
            },
          ]}
          rows={section.rows}
          getRowKey={(row) => `${row.category}:${row.source}:${row.exceptionClass}:${row.latestAt}:${row.message}`}
        />
      ) : (
        <EmptyRows />
      )}
    </SectionCard>
  )
}

export function StatusSections({ data }: { data: SystemStatusPayload }) {
  const sections = data.sections

  return (
    <>
      {sections.celery.available ? <CelerySection section={sections.celery} /> : <UnavailableSection message={sections.celery.error} />}
      {sections.agents.available ? <AgentSection section={sections.agents} /> : <UnavailableSection message={sections.agents.error} />}
      {sections.webSessions.available ? (
        <WebSessionSection section={sections.webSessions} />
      ) : (
        <UnavailableSection message={sections.webSessions.error} />
      )}
      {sections.compute.available ? <ComputeSection section={sections.compute} /> : <UnavailableSection message={sections.compute.error} />}
      {sections.proxies.available ? <ProxySection section={sections.proxies} /> : <UnavailableSection message={sections.proxies.error} />}
      {sections.browserTasks.available ? (
        <BrowserTaskSection section={sections.browserTasks} />
      ) : (
        <UnavailableSection message={sections.browserTasks.error} />
      )}
      {sections.agentErrors.available ? (
        <AgentErrorsSection section={sections.agentErrors} />
      ) : (
        <UnavailableSection message={sections.agentErrors.error} />
      )}
    </>
  )
}
