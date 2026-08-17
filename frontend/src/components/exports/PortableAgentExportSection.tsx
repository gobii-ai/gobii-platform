import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Archive, CheckCircle2, Download, Loader2 } from 'lucide-react'

import {
  fetchPortableAgentExport,
  fetchPortableAgentExports,
  startPortableAgentExport,
  type PortableAgentExportJob,
  type PortableAgentExportScope,
} from '../../api/agentExports'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { formatBytes } from '../../util/formatBytes'
import { formatAbsoluteTimestamp } from '../../util/time'
import { SettingsActionButton, SettingsStatusBadge } from '../agentSettings/SettingsControls'
import { AsyncActionConfirmDialog } from '../common/ActionConfirmDialog'

const ACTIVE_STATUSES = new Set<PortableAgentExportJob['status']>(['queued', 'running'])

type PortableAgentExportConfirmDialogProps = {
  open: boolean
  scope: PortableAgentExportScope
  agentId?: string | null
  onClose: () => void
  onStarted?: (job: PortableAgentExportJob) => void
}

export function PortableAgentExportConfirmDialog({
  open,
  scope,
  agentId = null,
  onClose,
  onStarted,
}: PortableAgentExportConfirmDialogProps) {
  const subject = scope === 'agent' ? 'this agent' : scope === 'personal' ? 'all personal agents' : 'all team agents'

  return (
    <AsyncActionConfirmDialog
      open={open}
      title={`Export ${subject}`}
      description="Gobii will prepare a portable ZIP in the background. You can leave this page while it is being created."
      icon={Download}
      confirmLabel="Start Export"
      onClose={onClose}
      onConfirm={async () => {
        const response = await startPortableAgentExport(scope, agentId)
        onStarted?.(response.export)
      }}
      getErrorMessage={(error) => safeErrorMessage(error, 'Unable to start the export.')}
      footerNote="We'll email you when it is ready."
      widthClass="sm:max-w-xl"
    >
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
        <p className="font-semibold">Before you export</p>
        <ul className="mt-2 list-disc space-y-1 pl-5">
          <li>Managed credentials and secret values are not included.</li>
          <li>Messages, files, SQLite data, and attachments may contain sensitive content.</li>
          <li>Schedules and integrations must be re-enabled or reconnected after import.</li>
        </ul>
      </div>
    </AsyncActionConfirmDialog>
  )
}

type PortableAgentExportSectionProps = {
  scope: PortableAgentExportScope
  agentId?: string | null
  surface?: 'settings' | 'profile'
  title?: string
  description?: string
  buttonLabel?: string
}

function jobStatus(job: PortableAgentExportJob): {
  label: string
  tone: 'neutral' | 'success' | 'warning' | 'danger'
} {
  if (job.status === 'queued') return { label: 'Queued', tone: 'neutral' }
  if (job.status === 'running') return { label: 'Exporting', tone: 'neutral' }
  if (job.status === 'ready') return { label: 'Ready', tone: 'success' }
  if (job.status === 'ready_with_warnings') return { label: 'Ready with warnings', tone: 'warning' }
  if (job.status === 'expired') return { label: 'Expired', tone: 'warning' }
  return { label: 'Failed', tone: 'danger' }
}

function ExportJobStatus({ job, surface }: { job: PortableAgentExportJob; surface: 'settings' | 'profile' }) {
  const status = jobStatus(job)
  const active = ACTIVE_STATUSES.has(job.status)
  const finishedAgents = job.agentsCompleted + job.agentsFailed
  const progress = job.agentsTotal > 0 ? Math.min(100, Math.round((finishedAgents / job.agentsTotal) * 100)) : 0
  const expiresAt = formatAbsoluteTimestamp(job.expiresAt)
  const createdAt = formatAbsoluteTimestamp(job.createdAt)
  const archiveSize = job.archiveSizeBytes === null ? null : formatBytes(job.archiveSizeBytes)
  const issueSummary = [
    job.warningCount > 0 ? `${job.warningCount} warning${job.warningCount === 1 ? '' : 's'}` : null,
    job.agentsFailed > 0
      ? `${job.agentsFailed} agent${job.agentsFailed === 1 ? '' : 's'} could not be exported`
      : null,
  ].filter(Boolean).join(' · ')

  return (
    <div className="space-y-3 rounded-lg border border-slate-200/70 bg-transparent p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {active ? (
            <Loader2 className="h-4 w-4 animate-spin text-sky-600" aria-hidden="true" />
          ) : job.status === 'ready' || job.status === 'ready_with_warnings' ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
          ) : (
            <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden="true" />
          )}
          <span className="text-sm font-semibold text-slate-800">Latest export</span>
          <SettingsStatusBadge surface={surface === 'settings' ? 'embedded' : 'standalone'} tone={status.tone}>
            {status.label}
          </SettingsStatusBadge>
        </div>
        {createdAt ? <span className="text-xs text-slate-500">Started {createdAt}</span> : null}
      </div>

      {active ? (
        <div className="space-y-2" aria-live="polite">
          <div className="h-2 overflow-hidden rounded-full bg-sky-100">
            <div className="h-full rounded-full bg-sky-600 transition-[width]" style={{ width: `${progress}%` }} />
          </div>
          <p className="text-xs text-slate-600">
            {job.phase || 'Preparing export'} · {finishedAgents} of {job.agentsTotal} agent{job.agentsTotal === 1 ? '' : 's'} processed
          </p>
        </div>
      ) : null}

      {issueSummary ? <p className="text-sm text-amber-800">{issueSummary}</p> : null}
      {job.redactionCount > 0 ? (
        <p className="text-sm text-slate-600">
          {job.redactionCount} sensitive value{job.redactionCount === 1 ? '' : 's'} removed from portable text and tool data.
        </p>
      ) : null}
      {job.error ? <p className="text-sm text-rose-700">{job.error}</p> : null}

      {(job.status === 'ready' || job.status === 'ready_with_warnings') && job.downloadUrl ? (
        <div className="flex flex-wrap items-center gap-3">
          <SettingsActionButton
            as="a"
            href={job.downloadUrl}
            surface={surface === 'settings' ? 'embedded' : 'standalone'}
            tone="primary"
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            Download ZIP
          </SettingsActionButton>
          <span className="text-xs text-slate-500">
            {[archiveSize, expiresAt ? `Expires ${expiresAt}` : null].filter(Boolean).join(' · ')}
          </span>
        </div>
      ) : null}
      {job.status === 'expired' ? <p className="text-sm text-slate-600">Start a new export to create another download.</p> : null}
    </div>
  )
}

export function PortableAgentExportSection({
  scope,
  agentId = null,
  surface = 'settings',
  title = 'Export Agent',
  description = 'Create a portable copy for moving to another agent platform.',
  buttonLabel = 'Export Agent',
}: PortableAgentExportSectionProps) {
  const [job, setJob] = useState<PortableAgentExportJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)

  const loadLatest = useCallback(async (signal?: AbortSignal) => {
    try {
      const exports = await fetchPortableAgentExports(scope, agentId, signal)
      setJob(exports[0] ?? null)
      setLoadError(null)
    } catch (error) {
      if (signal?.aborted) return
      setLoadError(safeErrorMessage(error, 'Unable to load recent exports.'))
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [agentId, scope])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    void loadLatest(controller.signal)
    return () => controller.abort()
  }, [loadLatest])

  const active = Boolean(job && ACTIVE_STATUSES.has(job.status))
  const activeJobId = active ? job?.id ?? null : null

  useEffect(() => {
    if (!activeJobId) return
    const controller = new AbortController()
    let refreshing = false
    const refresh = async () => {
      if (refreshing) return
      refreshing = true
      try {
        const nextJob = await fetchPortableAgentExport(activeJobId, controller.signal)
        setJob(nextJob)
        setLoadError(null)
      } catch (error) {
        if (!controller.signal.aborted) {
          setLoadError(safeErrorMessage(error, 'Unable to refresh export progress.'))
        }
      } finally {
        refreshing = false
      }
    }
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [activeJobId])

  const content = (
    <>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        {surface === 'settings' ? (
          <div>
            <h3 className="text-base font-semibold text-gray-800">{title}</h3>
            <p className="text-sm text-gray-500">{description}</p>
          </div>
        ) : null}
        <SettingsActionButton
          surface={surface === 'settings' ? 'embedded' : 'standalone'}
          tone="primary"
          responsive
          disabled={active || loading}
          onClick={() => setConfirmOpen(true)}
        >
          <Archive className="h-4 w-4" aria-hidden="true" />
          {active ? 'Export in progress' : buttonLabel}
        </SettingsActionButton>
      </div>
      {loading ? <p className="text-sm text-slate-500">Loading recent exports...</p> : null}
      {loadError ? <p className="text-sm text-rose-700">{loadError}</p> : null}
      {job ? <ExportJobStatus job={job} surface={surface} /> : null}
      <p className="text-xs text-slate-500">Completed downloads are available for seven days. You will receive an email when the ZIP is ready.</p>
      <PortableAgentExportConfirmDialog
        open={confirmOpen}
        scope={scope}
        agentId={agentId}
        onClose={() => setConfirmOpen(false)}
        onStarted={(nextJob) => {
          setJob(nextJob)
          setLoadError(null)
        }}
      />
    </>
  )

  if (surface === 'profile') {
    return (
      <section className="profile-screen__section">
        <div className="profile-screen__section-header">
          <div className="profile-screen__section-icon" aria-hidden="true">
            <Archive className="h-4 w-4" />
          </div>
          <div>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
        </div>
        <div className="space-y-4">{content}</div>
      </section>
    )
  }

  return <section className="space-y-4 px-5 py-5">{content}</section>
}
