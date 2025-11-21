import { useEffect, useState } from 'react'
import { AlertTriangle, Beaker, CheckCircle2, Loader2, XCircle } from 'lucide-react'

import { fetchSuiteRunDetail, type EvalRun, type EvalSuiteRun, type EvalTask } from '../api/evals'

type Status = 'pending' | 'running' | 'completed' | 'errored'

const statusLabel: Record<Status, string> = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Completed',
  errored: 'Errored',
}

const statusColor: Record<Status, string> = {
  pending: 'text-slate-600',
  running: 'text-blue-600',
  completed: 'text-emerald-600',
  errored: 'text-rose-600',
}

const statusIcon: Record<Status, JSX.Element> = {
  pending: <Loader2 className="w-4 h-4 animate-spin" />,
  running: <Loader2 className="w-4 h-4 animate-spin" />,
  completed: <CheckCircle2 className="w-4 h-4" />,
  errored: <XCircle className="w-4 h-4" />,
}

const formatTs = (value: string | null | undefined) => {
  if (!value) return '—'
  try {
    const date = new Date(value)
    return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`
  } catch {
    return value
  }
}

export function EvalsDetailScreen({ suiteRunId }: { suiteRunId: string }) {
  const [suite, setSuite] = useState<EvalSuiteRun | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const result = await fetchSuiteRunDetail(suiteRunId)
        if (!cancelled) {
          setSuite(result.suite_run)
        }
      } catch (err) {
        console.error(err)
        if (!cancelled) setError('Unable to load eval run details.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${protocol}://${window.location.host}/ws/evals/suites/${suiteRunId}/`)
    socket.onmessage = load
    socket.onerror = () => socket.close()

    return () => {
      cancelled = true
      socket.close()
    }
  }, [suiteRunId])

  return (
    <div className="app-shell">
      <header className="app-header card card--header">
        <div className="card__body card__body--header">
          <div className="flex items-center gap-2">
            <Beaker className="w-6 h-6 text-blue-600" />
            <h1 className="app-title">Eval Run Detail</h1>
          </div>
          <p className="app-subtitle">Suite run {suiteRunId}</p>
        </div>
      </header>

      {error && (
        <div className="card border-rose-200 bg-rose-50 text-rose-700">
          <div className="card__body flex items-start gap-2 text-sm">
            <AlertTriangle className="w-4 h-4 mt-0.5" />
            <div>{error}</div>
          </div>
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-sm text-slate-600">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading…
        </div>
      )}

      {suite && (
        <section className="card space-y-4">
          <div className="card__body flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">Suite</p>
              <p className="text-base font-semibold text-slate-900">{suite.suite_slug}</p>
              <p className="text-[11px] text-slate-500">Strategy: {suite.agent_strategy}</p>
            </div>
            <StatusPill status={(suite.status as Status) || 'pending'} />
          </div>

          <div className="card__body grid gap-3 sm:grid-cols-3">
            <Stat label="Started" value={formatTs(suite.started_at)} />
            <Stat label="Finished" value={formatTs(suite.finished_at)} />
            <Stat
              label="Runs completed"
              value={
                suite.run_totals
                  ? `${suite.run_totals.completed}/${suite.run_totals.total_runs}`
                  : suite.runs
                    ? `${suite.runs.filter((r) => r.status === 'completed').length}/${suite.runs.length}`
                    : '—'
              }
            />
          </div>

          <div className="card__body space-y-2">
            <h2 className="text-sm font-semibold text-slate-800">Scenarios</h2>
            <div className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
              {(suite.runs || []).map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
              {!suite.runs?.length && (
                <div className="p-3 text-sm text-slate-500">No scenario runs available.</div>
              )}
            </div>
          </div>
        </section>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className="text-sm font-semibold text-slate-800">{value}</p>
    </div>
  )
}

function StatusPill({ status }: { status: Status }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${statusColor[status]} border-slate-200 bg-slate-50`}
    >
      {statusIcon[status]}
      {statusLabel[status]}
    </span>
  )
}

function RunRow({ run }: { run: EvalRun }) {
  return (
    <div className="p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-900">{run.scenario_slug}</p>
          <p className="text-[11px] text-slate-500">
            Agent: {run.agent_id || 'ephemeral'} · Started {formatTs(run.started_at)}
          </p>
        </div>
        <StatusPill status={(run.status as Status) || 'pending'} />
      </div>
      {(run.tasks || []).length > 0 ? (
        <div className="mt-3 space-y-2">
          {run.tasks?.map((task) => (
            <TaskRow key={task.id} task={task} />
          ))}
        </div>
      ) : (
        <p className="mt-2 text-xs text-slate-500">Tasks not loaded yet.</p>
      )}
    </div>
  )
}

function TaskRow({ task }: { task: EvalTask }) {
  const isPass = task.status === 'passed'
  const isFail = task.status === 'failed' || task.status === 'errored'
  const statusCls = isPass ? 'text-emerald-700' : isFail ? 'text-rose-700' : 'text-slate-700'
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-slate-900">
            {task.sequence}. {task.name}
          </p>
          <p className="text-[11px] text-slate-500">Assertion: {task.assertion_type}</p>
        </div>
        <span className={`text-xs font-semibold ${statusCls}`}>{task.status}</span>
      </div>
      {task.observed_summary && <p className="mt-1 text-xs text-slate-600">{task.observed_summary}</p>}
    </div>
  )
}
