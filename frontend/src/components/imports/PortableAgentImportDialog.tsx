import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArchiveRestore, CheckCircle2, FileArchive, Loader2, Upload } from 'lucide-react'

import {
  discardPortableAgentImport,
  fetchPortableAgentImport,
  fetchPortableAgentImports,
  startPortableAgentImport,
  uploadPortableAgentImport,
  type PortableAgentImportJob,
} from '../../api/agentImports'
import { HttpError } from '../../api/http'
import { Modal } from '../common/Modal'

type PortableAgentImportDialogProps = {
  initialJobs?: PortableAgentImportJob[]
  onClose: () => void
}

const POLLED_STATUSES = new Set<PortableAgentImportJob['status']>(['validating', 'queued', 'running'])

function errorMessage(error: unknown): string {
  if (error instanceof HttpError && error.body && typeof error.body === 'object' && 'error' in error.body) {
    return String((error.body as { error?: unknown }).error || 'The request could not be completed.')
  }
  return error instanceof Error ? error.message : 'The request could not be completed.'
}

function formatBytes(value: number | null): string {
  if (!value) return 'Unknown size'
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export function PortableAgentImportDialog({ initialJobs = [], onClose }: PortableAgentImportDialogProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [job, setJob] = useState<PortableAgentImportJob | null>(initialJobs[0] ?? null)
  const [loading, setLoading] = useState(initialJobs.length === 0)
  const [busy, setBusy] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [names, setNames] = useState<Record<string, string>>({})

  useEffect(() => {
    if (initialJobs.length > 0) return
    const controller = new AbortController()
    void fetchPortableAgentImports(controller.signal)
      .then((jobs) => setJob(jobs[0] ?? null))
      .catch((reason: unknown) => setError(errorMessage(reason)))
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [initialJobs.length])

  useEffect(() => {
    if (!job || !POLLED_STATUSES.has(job.status)) return
    const controller = new AbortController()
    const timer = window.setInterval(() => {
      void fetchPortableAgentImport(job.id, controller.signal)
        .then(setJob)
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) setError(errorMessage(reason))
        })
    }, 1500)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [job])

  useEffect(() => {
    if (job?.status !== 'awaiting_selection') return
    setSelected((current) => {
      if (Object.keys(current).length > 0) return current
      return Object.fromEntries(job.agents.filter((agent) => agent.selectable).map((agent) => [agent.id, true]))
    })
    setNames((current) => ({
      ...Object.fromEntries(job.agents.map((agent) => [agent.id, agent.proposedName])),
      ...current,
    }))
  }, [job])

  const chosenAgents = useMemo(() => {
    if (!job) return []
    return job.agents.filter((agent) => agent.selectable && selected[agent.id])
  }, [job, selected])

  const resetForUpload = useCallback(() => {
    setJob(null)
    setSelected({})
    setNames({})
    setUploadProgress(0)
    setError(null)
  }, [])

  const handleFile = useCallback(async (file: File | undefined) => {
    if (!file) return
    setBusy(true)
    setError(null)
    setUploadProgress(1)
    try {
      setJob(await uploadPortableAgentImport(file, setUploadProgress))
    } catch (reason) {
      setError(errorMessage(reason))
      setUploadProgress(0)
    } finally {
      setBusy(false)
    }
  }, [])

  const handleStart = useCallback(async () => {
    if (!job) return
    const agents = chosenAgents.map((agent) => ({ itemId: agent.id, name: (names[agent.id] ?? '').trim() }))
    if (agents.some((agent) => !agent.name)) {
      setError('Every selected agent needs a name.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const response = await startPortableAgentImport(job.id, agents)
      setJob(response.import)
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusy(false)
    }
  }, [chosenAgents, job, names])

  const handleDiscard = useCallback(async () => {
    if (!job) return
    setBusy(true)
    setError(null)
    try {
      await discardPortableAgentImport(job.id)
      resetForUpload()
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setBusy(false)
    }
  }, [job, resetForUpload])

  const terminal = job && ['completed', 'completed_with_warnings', 'failed', 'expired'].includes(job.status)
  const importedAgents = job?.agents.filter((agent) => agent.importedAgent) ?? []
  const progressTotal = Math.max(job?.agentsSelected ?? 0, 1)
  const progressDone = (job?.agentsCompleted ?? 0) + (job?.agentsFailed ?? 0)

  return (
    <Modal
      title="Import agents"
      subtitle="Restore a Gobii portable-export ZIP into the current workspace."
      icon={ArchiveRestore}
      iconBgClass="bg-blue-100"
      iconColorClass="text-blue-700"
      widthClass="sm:max-w-3xl"
      onClose={onClose}
      dismissible={!busy}
    >
      <div className="space-y-5">
        {error ? (
          <div className="rounded-xl bg-red-100 px-4 py-3 text-sm text-red-800" role="alert">{error}</div>
        ) : null}

        {loading ? (
          <div className="flex items-center gap-3 py-8 text-sm text-slate-600">
            <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
            Checking recent imports…
          </div>
        ) : !job ? (
          <div className="space-y-4">
            <button
              type="button"
              className="flex w-full flex-col items-center gap-3 rounded-2xl border-2 border-dashed border-blue-300 bg-blue-50 px-6 py-10 text-center hover:border-blue-500"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy}
            >
              <Upload className="h-8 w-8 text-blue-700" aria-hidden="true" />
              <span className="font-semibold text-slate-900">Choose a portable-export ZIP</span>
              <span className="max-w-lg text-sm text-slate-600">Only Gobii exports are accepted. The archive is validated in the background before anything is created.</span>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,application/zip"
              className="hidden"
              onChange={(event) => void handleFile(event.target.files?.[0])}
            />
            {uploadProgress > 0 ? (
              <div aria-label={`Upload ${uploadProgress}%`}>
                <div className="mb-2 flex justify-between text-sm text-slate-700"><span>Uploading</span><span>{uploadProgress}%</span></div>
                <div className="h-2 overflow-hidden rounded-full bg-blue-100"><div className="h-full rounded-full bg-blue-600" style={{ width: `${uploadProgress}%` }} /></div>
              </div>
            ) : null}
          </div>
        ) : job.status === 'validating' ? (
          <div className="flex items-start gap-4 rounded-2xl bg-blue-50 p-5">
            <Loader2 className="mt-0.5 h-6 w-6 animate-spin text-blue-700" aria-hidden="true" />
            <div><p className="font-semibold text-slate-900">Validating {job.archiveName}</p><p className="mt-1 text-sm text-slate-600">Checking paths, size limits, manifests, and every file checksum.</p></div>
          </div>
        ) : job.status === 'awaiting_selection' ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-blue-50 px-4 py-3 text-sm">
              <div><span className="font-semibold text-slate-900">Destination:</span> <span className="text-slate-700">{job.target.name}</span></div>
              <div className="text-slate-700">{job.capacityAvailable} agent slot{job.capacityAvailable === 1 ? '' : 's'} available</div>
            </div>
            <div className="flex items-center gap-3 text-sm text-slate-600">
              <FileArchive className="h-5 w-5 text-slate-700" aria-hidden="true" />
              <span>{job.archiveName} · {formatBytes(job.archiveSizeBytes)} · {job.formatVersion}</span>
            </div>
            <div className="space-y-3">
              {job.agents.map((agent) => (
                <div key={agent.id} className={`rounded-xl border p-4 ${agent.selectable ? 'border-slate-200 bg-white' : 'border-amber-300 bg-amber-50'}`}>
                  <div className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600"
                      checked={Boolean(selected[agent.id])}
                      disabled={!agent.selectable}
                      onChange={(event) => setSelected((current) => ({ ...current, [agent.id]: event.target.checked }))}
                      aria-label={`Import ${agent.sourceName}`}
                    />
                    <div className="min-w-0 flex-1">
                      <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500" htmlFor={`import-name-${agent.id}`}>Agent name</label>
                      <input
                        id={`import-name-${agent.id}`}
                        value={names[agent.id] ?? agent.proposedName}
                        disabled={!agent.selectable || !selected[agent.id]}
                        maxLength={255}
                        onChange={(event) => setNames((current) => ({ ...current, [agent.id]: event.target.value }))}
                        className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 disabled:opacity-60"
                      />
                      <p className="mt-2 text-xs text-slate-500">{agent.messageCount} messages · {agent.stepCount} tool steps · {agent.fileCount} files</p>
                      {agent.error ? <p className="mt-2 text-sm text-amber-800">{agent.error}</p> : null}
                      {agent.warnings.map((warning) => <p key={warning} className="mt-2 text-sm text-amber-800">{warning}</p>)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
            {chosenAgents.length > job.capacityAvailable ? (
              <p className="rounded-xl bg-amber-100 px-4 py-3 text-sm text-amber-900">Select no more than {job.capacityAvailable} agent{job.capacityAvailable === 1 ? '' : 's'} for this workspace.</p>
            ) : null}
            <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-between">
              <button type="button" className="rounded-lg px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-blue-50" onClick={() => void handleDiscard()} disabled={busy}>Discard upload</button>
              <button
                type="button"
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
                onClick={() => void handleStart()}
                disabled={busy || chosenAgents.length === 0 || chosenAgents.length > job.capacityAvailable}
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <ArchiveRestore className="h-4 w-4" aria-hidden="true" />}
                Import {chosenAgents.length || ''} agent{chosenAgents.length === 1 ? '' : 's'}
              </button>
            </div>
          </div>
        ) : job.status === 'queued' || job.status === 'running' ? (
          <div className="space-y-4 rounded-2xl bg-blue-50 p-5">
            <div className="flex items-start gap-4">
              <Loader2 className="mt-0.5 h-6 w-6 animate-spin text-blue-700" aria-hidden="true" />
              <div className="flex-1"><p className="font-semibold text-slate-900">Importing agents</p><p className="mt-1 text-sm text-slate-600">Successful agents become available for web chat as soon as their restoration finishes.</p></div>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-blue-100"><div className="h-full rounded-full bg-blue-600 transition-all" style={{ width: `${Math.max(5, Math.round((progressDone / progressTotal) * 100))}%` }} /></div>
            <p className="text-sm text-slate-700">{progressDone} of {job.agentsSelected} finished</p>
          </div>
        ) : terminal ? (
          <div className="space-y-5">
            <div className={`flex items-start gap-4 rounded-2xl p-5 ${importedAgents.length > 0 ? 'bg-emerald-50' : 'bg-red-100'}`}>
              {importedAgents.length > 0 ? <CheckCircle2 className="mt-0.5 h-6 w-6 text-emerald-700" aria-hidden="true" /> : <AlertTriangle className="mt-0.5 h-6 w-6 text-red-700" aria-hidden="true" />}
              <div><p className="font-semibold text-slate-900">{importedAgents.length > 0 ? `${importedAgents.length} agent${importedAgents.length === 1 ? '' : 's'} imported` : 'Import not completed'}</p><p className="mt-1 text-sm text-slate-700">{job.error || 'Review warnings and reconnect anything that remains disabled.'}</p></div>
            </div>
            {importedAgents.map((item) => (
              <div key={item.id} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div><p className="font-semibold text-slate-900">{item.importedAgent?.name}</p><p className="mt-1 text-xs text-slate-500">Web chat ready · automation and connections off</p></div>
                  <a href={item.importedAgent?.url} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700">Open agent</a>
                </div>
                {item.warnings.map((warning) => <p key={warning} className="mt-2 text-sm text-amber-800">{warning}</p>)}
              </div>
            ))}
            {job.agents.filter((item) => item.error).map((item) => <p key={item.id} className="rounded-xl bg-red-100 px-4 py-3 text-sm text-red-800"><strong>{item.sourceName}:</strong> {item.error}</p>)}
            <div className="flex justify-end gap-3">
              <button type="button" className="rounded-lg px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-blue-50" onClick={resetForUpload}>Import another export</button>
              <button type="button" className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700" onClick={onClose}>Done</button>
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
