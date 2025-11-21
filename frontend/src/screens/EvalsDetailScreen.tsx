import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Beaker, Loader2, RefreshCcw, ArrowLeft } from 'lucide-react'

import { fetchSuiteRunDetail, updateSuiteRunType, type EvalRun, type EvalSuiteRun, type EvalTask } from '../api/evals'
import { StatusBadge } from '../components/common/StatusBadge'
import { RunTypeBadge } from '../components/common/RunTypeBadge'

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
  const [updatingRunType, setUpdatingRunType] = useState(false)

  const hasRuns = useMemo(() => Boolean(suite?.runs && suite.runs.length), [suite?.runs])

  const completionStats = useMemo(() => {
    if (!suite) return { total: 0, completed: 0 }
    if (suite.runs && suite.runs.length > 0) {
      return {
        total: suite.runs.length,
        completed: suite.runs.filter((r) => r.status === 'completed').length,
      }
    }
    return {
      total: suite.run_totals?.total_runs ?? 0,
      completed: suite.run_totals?.completed ?? 0,
    }
  }, [suite])

  const toggleRunType = async (nextRunType: EvalSuiteRun['run_type']) => {
    setUpdatingRunType(true)
    setError(null)
    try {
      const result = await updateSuiteRunType(suiteRunId, {
        run_type: nextRunType,
        official: nextRunType === 'official',
      })
      setSuite(result.suite_run)
    } catch (err) {
      console.error(err)
      setError('Unable to update run type right now.')
    } finally {
      setUpdatingRunType(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    const load = async (background = false) => {
      if (!background) {
        setLoading(true)
        setError(null)
      }
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
    load(false)

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${protocol}://${window.location.host}/ws/evals/suites/${suiteRunId}/`)
    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        const payload = data?.payload
        if (!payload) return

        // If we got a full suite payload, replace state
        if (payload.suite_slug) {
          setSuite((prev) => {
            // prefer incoming runs/tasks if present
            if (!prev) return payload as EvalSuiteRun
            return {
              ...prev,
              ...payload,
            }
          })
          return
        }

        // If we got run/task updates, patch in-place to avoid re-fetches
        setSuite((prev) => {
          if (!prev) return prev
          // Run update
          if (payload.run_id || payload.scenario_slug || payload.status) {
            const runId = payload.id || payload.run_id
            const updatedRuns = (prev.runs || []).map((run) =>
              run.id === runId ? { ...run, ...payload } : run,
            )
            return { ...prev, runs: updatedRuns }
          }
          // Task update
          if (payload.sequence !== undefined && payload.run_id) {
            const updatedRuns = (prev.runs || []).map((run) => {
              if (run.id !== payload.run_id) return run
              const tasks = run.tasks || []
              const found = tasks.find((t) => t.id === payload.id)
              const nextTasks = found
                ? tasks.map((t) => (t.id === payload.id ? { ...t, ...payload } : t))
                : [...tasks, payload as EvalTask]
              return { ...run, tasks: nextTasks }
            })
            return { ...prev, runs: updatedRuns }
          }
          return prev
        })
      } catch (err) {
        console.error('Failed to process eval websocket message', err)
      }
    }
    socket.onerror = () => socket.close()

    return () => {
      cancelled = true
      socket.close()
    }
  }, [suiteRunId])

  return (
    <div className="app-shell">
      <div className="card card--header">
        <div className="card__body card__body--header flex flex-col sm:flex-row sm:items-center justify-between gap-4 py-4 sm:py-3">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-white/90 rounded-xl shadow-sm text-blue-700">
              <Beaker className="w-6 h-6" />
            </div>
            <div>
              <div className="flex items-center gap-3">
                <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Eval Run Detail</h1>
                {suite && <StatusBadge status={suite.status || 'pending'} />}
                {suite && <RunTypeBadge runType={suite.run_type} />}
              </div>
              <p className="text-slate-600 mt-1.5 flex items-center gap-2">
                Inspect individual scenario runs and task assertions.
                <span className="text-slate-300">•</span>
                <span className="font-mono text-xs text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">{suiteRunId}</span>
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <a
              href="/console/evals/"
              className="inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-200 rounded-lg shadow-sm hover:bg-slate-50 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-all"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </a>
            <button
              type="button"
              className="inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-200 rounded-lg shadow-sm hover:bg-slate-50 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-all"
              onClick={() => {
                fetchSuiteRunDetail(suiteRunId)
                  .then((res) => setSuite(res.suite_run))
                  .catch((err) => {
                    console.error(err)
                    setError('Unable to refresh right now.')
                  })
              }}
            >
              <RefreshCcw className="w-4 h-4" />
              Refresh
            </button>
            {suite && (
              <button
                type="button"
                className="inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-semibold text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-lg shadow-sm hover:bg-emerald-100 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-emerald-500 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                onClick={() => toggleRunType(suite.run_type === 'official' ? 'one_off' : 'official')}
                disabled={updatingRunType}
              >
                {updatingRunType ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                {suite.run_type === 'official' ? 'Mark as One-off' : 'Mark as Official'}
              </button>
            )}
          </div>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-700 shadow-sm">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 mt-0.5 shrink-0" />
            <div className="text-sm font-medium">{error}</div>
          </div>
        </div>
      )}

      {loading && !suite && (
        <div className="flex flex-col items-center justify-center py-24 gap-4 text-sm text-slate-600">
          <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
          <p className="font-medium">Loading evaluation results...</p>
        </div>
      )}

      {suite && (
        <>
          <section className="card overflow-hidden" style={{ padding: 0 }}>
            <div className="bg-gradient-to-r from-blue-50/80 to-indigo-50/80 border-b border-blue-100 px-6 py-4">
              <h2 className="text-base font-bold text-slate-900 uppercase tracking-wide">Overview</h2>
            </div>
            <div className="p-6 grid gap-6 sm:grid-cols-3">
              <div className="flex flex-col justify-between space-y-2">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400">Suite Strategy</p>
                  <p className="text-lg font-bold text-slate-900 mt-1">{suite.suite_slug}</p>
                </div>
                <p className="text-sm text-slate-500">
                  Strategy: <span className="font-medium text-slate-700">{suite.agent_strategy}</span>
                </p>
                <div className="space-y-1">
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400">Run Type</p>
                  <div className="flex items-center gap-2">
                    <RunTypeBadge runType={suite.run_type} />
                    <span className="text-xs text-slate-500">
                      {suite.run_type === 'official' ? 'Tracked for metrics' : 'Ad-hoc validation'}
                    </span>
                  </div>
                </div>
              </div>

              <div className="flex flex-col justify-between space-y-2 sm:pl-6 sm:border-l sm:border-slate-100">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400">Timing</p>
                  <div className="space-y-1 mt-1">
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-500">Started:</span>
                      <span className="font-mono text-slate-700">{formatTs(suite.started_at)}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-slate-500">Finished:</span>
                      <span className="font-mono text-slate-700">{formatTs(suite.finished_at)}</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex flex-col justify-between space-y-2 sm:pl-6 sm:border-l sm:border-slate-100">
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400">Completion</p>
                  <div className="flex items-baseline gap-2 mt-1">
                    <span className="text-3xl font-bold text-slate-900">
                      {completionStats.completed}
                    </span>
                    <span className="text-sm font-medium text-slate-500">
                      / {completionStats.total} runs
                    </span>
                  </div>
                </div>
                <div className="h-2 w-full bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 transition-all duration-700 ease-out"
                    style={{ width: `${completionStats.total ? (completionStats.completed / completionStats.total) * 100 : 0}%` }}
                  />
                </div>
              </div>
            </div>
          </section>

          {/* Scenarios section */}
          <section className="card overflow-hidden" style={{ padding: 0 }}>
            <div className="bg-gradient-to-r from-blue-50/80 to-indigo-50/80 border-b border-blue-100 px-6 py-4">
              <h2 className="text-base font-bold text-slate-900 uppercase tracking-wide">Scenarios</h2>
            </div>
            <div className="divide-y divide-slate-100">
              {(suite.runs || []).map((run) => (
                <RunCard key={run.id} run={run} />
              ))}
              {!hasRuns && (
                <div className="p-12 text-center text-slate-500 font-medium">
                  No scenario runs available for this suite.
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  )
}

function RunCard({ run }: { run: EvalRun }) {
  const [expanded, setExpanded] = useState(true)
  
  const isCompleted = run.status === 'completed'
  const isRunning = run.status === 'running'
  
  return (
    <div className="bg-white transition-colors hover:bg-slate-50 group">
      <div 
        className="flex flex-wrap items-center justify-between gap-4 p-6 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-4">
           <div className={`w-3 h-3 rounded-full shadow-sm shrink-0 ${isCompleted ? 'bg-emerald-500' : isRunning ? 'bg-blue-500' : 'bg-slate-300'}`} />
           <div>
              <h3 className="text-base font-bold text-slate-900 group-hover:text-blue-700 transition-colors">{run.scenario_slug}</h3>
              <div className="text-xs text-slate-500 mt-1 flex flex-wrap items-center gap-2">
                <RunTypeBadge runType={run.run_type} dense />
                <span className="flex items-center gap-1">
                  Agent:
                  <span className="font-mono text-slate-600 bg-slate-100 px-1.5 rounded ring-1 ring-slate-200">{run.agent_id || 'ephemeral'}</span>
                </span>
              </div>
           </div>
        </div>
        <div className="flex items-center gap-4">
           <div className="text-right hidden sm:block">
             <div className="text-[10px] uppercase tracking-wider text-slate-400 font-bold">Duration</div>
             <div className="text-xs font-mono text-slate-700">
                {run.finished_at && run.started_at 
                  ? ((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000).toFixed(1) + 's'
                  : '—'
                }
             </div>
           </div>
           <StatusBadge status={run.status || 'pending'} />
        </div>
      </div>
      
      {expanded && (
        <div className="bg-slate-50 border-t border-slate-100 px-6 py-6">
          {(run.tasks || []).length > 0 ? (
            <div className="space-y-3">
              {run.tasks?.map((task) => (
                <TaskRow key={task.id} task={task} />
              ))}
            </div>
          ) : (
            <p className="py-2 text-xs italic text-slate-400 text-center">
              Tasks not loaded or empty.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function TaskRow({ task }: { task: EvalTask }) {
  const isPass = task.status === 'passed'
  const isFail = task.status === 'failed' || task.status === 'errored'
  
  return (
    <div className={`
      group flex items-start gap-3 rounded-lg p-4 text-sm transition-all
      ${isPass ? 'ring-1 ring-inset ring-emerald-200 bg-emerald-50' : ''}
      ${isFail ? 'ring-1 ring-inset ring-rose-200 bg-rose-50' : ''}
      ${!isPass && !isFail ? 'ring-1 ring-inset ring-slate-200 bg-white' : ''}
    `}>
      <div className="mt-0.5 shrink-0">
         <StatusBadge status={task.status} animate={false} className="bg-white shadow-sm ring-1 ring-slate-200" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex justify-between items-start gap-2">
           <p className="font-semibold text-slate-900 break-words">
             <span className="font-mono text-xs text-slate-400 mr-2">#{task.sequence}</span>
             {task.name}
           </p>
           <span className="shrink-0 text-[10px] font-mono text-slate-500 bg-white/50 px-1.5 py-0.5 rounded ring-1 ring-slate-200/50">{task.assertion_type}</span>
        </div>
        
        {task.observed_summary && (
          <div className={`mt-2 text-xs p-2.5 rounded bg-white/60 ring-1 ring-black/5 leading-relaxed font-mono ${isFail ? 'text-rose-800' : 'text-slate-600'}`}>
            {task.observed_summary}
          </div>
        )}
      </div>
    </div>
  )
}
