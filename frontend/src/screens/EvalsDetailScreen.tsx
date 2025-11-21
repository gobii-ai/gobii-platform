import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Beaker, Loader2, RefreshCcw } from 'lucide-react'

import { fetchSuiteRunDetail, type EvalRun, type EvalSuiteRun, type EvalTask } from '../api/evals'
import { StatusBadge } from '../components/common/StatusBadge'

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

  const hasRuns = useMemo(() => Boolean(suite?.runs && suite.runs.length), [suite?.runs])

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
      <div className="max-w-5xl mx-auto space-y-6 pb-12 px-4 sm:px-6">
        <header className="card card--header">
          <div className="card__body card__body--header flex items-center justify-between gap-4 flex-wrap">
            <div className="flex items-start gap-3">
               <div className="p-2 rounded-lg bg-blue-50 text-blue-600 border border-blue-100">
                 <Beaker className="w-5 h-5" />
               </div>
               <div>
                 <div className="flex items-center gap-2">
                    <h1 className="text-lg font-bold text-slate-900">Eval Run Detail</h1>
                    {suite && <StatusBadge status={suite.status || 'pending'} />}
                 </div>
                 <p className="text-sm text-slate-500 font-mono mt-0.5">{suiteRunId}</p>
               </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="btn btn--secondary gap-2 text-xs py-1.5"
                onClick={() => {
                  fetchSuiteRunDetail(suiteRunId)
                    .then((res) => setSuite(res.suite_run))
                    .catch((err) => {
                      console.error(err)
                      setError('Unable to refresh right now.')
                    })
                }}
              >
                <RefreshCcw className="w-3.5 h-3.5" />
                Refresh
              </button>
            </div>
          </div>
        </header>

        {error && (
          <div className="card border-red-200 bg-red-50 text-red-700">
            <div className="card__body flex items-start gap-2 text-sm">
              <AlertTriangle className="w-4 h-4 mt-0.5" />
              <div>{error}</div>
            </div>
          </div>
        )}

        {loading && !suite && (
          <div className="flex flex-col items-center justify-center py-12 gap-3 text-sm text-slate-600">
            <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
            <p>Loading evaluation results…</p>
          </div>
        )}

        {suite && (
          <>
            <section className="grid gap-4 sm:grid-cols-3">
              <div className="card p-4 space-y-1">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Suite</p>
                <p className="text-lg font-bold text-slate-900">{suite.suite_slug}</p>
                <p className="text-xs text-slate-500">Strategy: <span className="font-medium">{suite.agent_strategy}</span></p>
              </div>
              <div className="card p-4 space-y-1">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Timing</p>
                <div className="text-sm space-y-0.5">
                  <div className="flex justify-between">
                    <span className="text-slate-500">Started:</span>
                    <span className="font-medium text-slate-900">{formatTs(suite.started_at)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Finished:</span>
                    <span className="font-medium text-slate-900">{formatTs(suite.finished_at)}</span>
                  </div>
                </div>
              </div>
              <div className="card p-4 space-y-1">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Results</p>
                <div className="flex items-baseline gap-2">
                  <span className="text-2xl font-bold text-slate-900">
                    {suite.run_totals
                        ? suite.run_totals.completed
                        : suite.runs?.filter((r) => r.status === 'completed').length ?? 0}
                  </span>
                  <span className="text-sm text-slate-500">
                    / {suite.run_totals ? suite.run_totals.total_runs : suite.runs?.length ?? 0} runs
                  </span>
                </div>
                <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden mt-2">
                   <div 
                      className="h-full bg-blue-500 transition-all duration-500"
                      style={{ width: `${suite.run_totals ? (suite.run_totals.completed / suite.run_totals.total_runs) * 100 : 0}%`}} 
                   />
                </div>
              </div>
            </section>

            <section className="space-y-4">
              <h2 className="text-lg font-semibold text-slate-800 px-1">Scenarios</h2>
              <div className="space-y-3">
                {(suite.runs || []).map((run) => (
                  <RunCard key={run.id} run={run} />
                ))}
                {!hasRuns && (
                  <div className="card p-8 text-center text-slate-500">No scenario runs available.</div>
                )}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  )
}

function RunCard({ run }: { run: EvalRun }) {
  const [expanded, setExpanded] = useState(true)
  
  const isCompleted = run.status === 'completed'
  const isRunning = run.status === 'running'
  
  return (
    <div className="card overflow-hidden transition-shadow hover:shadow-md">
      <div 
        className="card__body flex flex-wrap items-center justify-between gap-3 cursor-pointer bg-slate-50/50 hover:bg-slate-50"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-3">
           <div className={`w-1.5 h-1.5 rounded-full ${isCompleted ? 'bg-emerald-500' : isRunning ? 'bg-blue-500' : 'bg-slate-300'}`} />
           <div>
              <h3 className="text-sm font-bold text-slate-900">{run.scenario_slug}</h3>
              <p className="text-xs text-slate-500">
                Agent: <span className="font-mono text-slate-600">{run.agent_id || 'ephemeral'}</span>
              </p>
           </div>
        </div>
        <div className="flex items-center gap-4">
           <div className="text-right hidden sm:block">
             <div className="text-[10px] uppercase text-slate-400 font-semibold">Duration</div>
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
        <div className="border-t border-slate-100 bg-white px-4 py-3">
          {(run.tasks || []).length > 0 ? (
            <div className="space-y-2">
              {run.tasks?.map((task) => (
                <TaskRow key={task.id} task={task} />
              ))}
            </div>
          ) : (
            <p className="py-2 text-xs italic text-slate-400 text-center">Tasks not loaded or empty.</p>
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
      group flex items-start gap-3 rounded-md border p-3 text-sm transition-colors
      ${isPass ? 'border-emerald-100 bg-emerald-50/30' : ''}
      ${isFail ? 'border-rose-100 bg-rose-50/30' : ''}
      ${!isPass && !isFail ? 'border-slate-100 bg-slate-50' : ''}
    `}>
      <div className="mt-0.5">
         <StatusBadge status={task.status} animate={false} className="bg-white shadow-sm border-opacity-50" />
      </div>
      <div className="flex-1 space-y-1">
        <div className="flex justify-between items-start">
           <p className="font-medium text-slate-900">
             {task.sequence}. {task.name}
           </p>
           <span className="text-[10px] font-mono text-slate-400">{task.assertion_type}</span>
        </div>
        
        {task.observed_summary && (
          <p className={`text-xs ${isFail ? 'text-rose-700 font-medium' : 'text-slate-600'}`}>
            {task.observed_summary}
          </p>
        )}
      </div>
    </div>
  )
}