import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Beaker, CircleDot, Loader2, Play, RefreshCcw, CheckCircle2, XCircle } from 'lucide-react'

import {
  createSuiteRuns,
  fetchSuiteRuns,
  fetchSuites,
  type EvalSuite,
  type EvalSuiteRun,
} from '../api/evals'

type Status = 'pending' | 'running' | 'completed' | 'errored'

const statusStyles: Record<Status, { bg: string; text: string; icon: JSX.Element }> = {
  pending: { bg: 'bg-slate-100 text-slate-700', text: 'Pending', icon: <CircleDot className="w-4 h-4" /> },
  running: { bg: 'bg-blue-100 text-blue-700', text: 'Running', icon: <Loader2 className="w-4 h-4 animate-spin" /> },
  completed: { bg: 'bg-emerald-100 text-emerald-700', text: 'Completed', icon: <CheckCircle2 className="w-4 h-4" /> },
  errored: { bg: 'bg-rose-100 text-rose-700', text: 'Errored', icon: <XCircle className="w-4 h-4" /> },
}

function StatusBadge({ status }: { status: Status }) {
  const preset = statusStyles[status] ?? statusStyles.pending
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${preset.bg}`}>
      {preset.icon}
      {preset.text}
    </span>
  )
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

const pluralize = (count: number, word: string) => `${count} ${count === 1 ? word : `${word}s`}`

export function EvalsScreen() {
  const [suites, setSuites] = useState<EvalSuite[]>([])
  const [suiteRuns, setSuiteRuns] = useState<EvalSuiteRun[]>([])
  const [selectedSuites, setSelectedSuites] = useState<Set<string>>(new Set())
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [launching, setLaunching] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const listRefreshInFlight = useRef(false)

  const loadSuites = useCallback(async () => {
    try {
      const result = await fetchSuites()
      setSuites(result.suites)
      if (!selectedSuites.size && result.suites.length) {
        const defaultSuite =
          result.suites.find((suite) => suite.slug === 'core') ||
          result.suites.find((suite) => suite.slug === 'all') ||
          result.suites[0]
        if (defaultSuite) {
          setSelectedSuites(new Set([defaultSuite.slug]))
        }
      }
    } catch (error) {
      console.error(error)
      setErrorMessage('Unable to load suites right now.')
    }
  }, [selectedSuites.size])

  const loadSuiteRuns = useCallback(async () => {
    if (listRefreshInFlight.current) return
    listRefreshInFlight.current = true
    setLoadingRuns(true)
    try {
      const result = await fetchSuiteRuns({ limit: 25 })
      setSuiteRuns(result.suite_runs)
    } catch (error) {
      console.error(error)
      setErrorMessage('Unable to load suite runs right now.')
    } finally {
      setLoadingRuns(false)
      listRefreshInFlight.current = false
    }
  }, [])

  useEffect(() => {
    loadSuites()
    loadSuiteRuns()
  }, [loadSuites, loadSuiteRuns])

  const toggleSuiteSelection = (slug: string) => {
    setSelectedSuites((prev) => {
      const next = new Set(prev)
      if (next.has(slug)) {
        next.delete(slug)
      } else {
        next.add(slug)
      }
      return next
    })
  }

  const handleLaunch = async () => {
    setLaunching(true)
    setErrorMessage(null)
    try {
      const suite_slugs = selectedSuites.size ? Array.from(selectedSuites) : ['all']
      await createSuiteRuns({ suite_slugs, agent_strategy: 'ephemeral_per_scenario' })
      await loadSuiteRuns()
    } catch (error) {
      console.error(error)
      setErrorMessage('Failed to launch evals.')
    } finally {
      setLaunching(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header card card--header">
        <div className="card__body card__body--header">
          <div className="app-header__title">
            <div className="flex items-center gap-2">
              <Beaker className="w-6 h-6 text-blue-600" />
              <h1 className="app-title">Evals</h1>
            </div>
            <p className="app-subtitle">Run suites concurrently, watch progress, and inspect tasks.</p>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-60"
              onClick={loadSuiteRuns}
              disabled={loadingRuns}
            >
              <RefreshCcw className="w-4 h-4" />
              Refresh
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-60"
              onClick={handleLaunch}
              disabled={launching || suites.length === 0}
            >
              <Play className="w-4 h-4" />
              Launch
            </button>
          </div>
        </div>
      </header>

      {errorMessage && (
        <div className="card border-red-200 bg-red-50 text-red-700">
          <div className="card__body flex items-start gap-2 text-sm">
            <AlertTriangle className="w-4 h-4 mt-0.5" />
            <div>{errorMessage}</div>
          </div>
        </div>
      )}

      <section className="card">
        <div className="card__body space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-800">Select Suites</h2>
            <p className="text-xs text-slate-500">Default strategy: ephemeral agent per scenario.</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-3">
            {suites.map((suite) => {
              const checked = selectedSuites.has(suite.slug)
              return (
                <label
                  key={suite.slug}
                  className={`flex cursor-pointer flex-col gap-2 rounded-lg border p-3 transition ${
                    checked ? 'border-blue-400 bg-blue-50/60' : 'border-slate-200 bg-white hover:border-blue-200'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                      checked={checked}
                      onChange={() => toggleSuiteSelection(suite.slug)}
                    />
                    <span className="font-medium text-slate-800">{suite.slug}</span>
                  </div>
                  <p className="text-xs text-slate-600">{suite.description}</p>
                  <p className="text-[11px] text-slate-500">
                    {pluralize(suite.scenario_slugs.length, 'scenario')}
                  </p>
                </label>
              )
            })}
            {!suites.length && <div className="text-sm text-slate-500">No suites registered.</div>}
          </div>
        </div>
      </section>

      <section className="card">
        <div className="card__body space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-800">Recent Suite Runs</h2>
            {loadingRuns && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
          </div>

          <div className="overflow-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2">Suite</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Runs</th>
                  <th className="px-3 py-2">Started</th>
                  <th className="px-3 py-2">Finished</th>
                  <th className="px-3 py-2">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {suiteRuns.map((suite) => (
                  <tr key={suite.id} className="hover:bg-slate-50">
                    <td className="px-3 py-2 font-medium text-slate-800">{suite.suite_slug}</td>
                    <td className="px-3 py-2">
                      <StatusBadge status={(suite.status as Status) || 'pending'} />
                    </td>
                    <td className="px-3 py-2 text-slate-700">
                      {suite.run_totals
                        ? `${suite.run_totals.completed}/${suite.run_totals.total_runs} completed`
                        : '—'}
                    </td>
                    <td className="px-3 py-2 text-slate-600">{formatTs(suite.started_at)}</td>
                    <td className="px-3 py-2 text-slate-600">{formatTs(suite.finished_at)}</td>
                    <td className="px-3 py-2">
                      <a
                        className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                        href={`/console/evals/${suite.id}/`}
                      >
                        View
                      </a>
                    </td>
                  </tr>
                ))}
                {!suiteRuns.length && (
                  <tr>
                    <td className="px-3 py-3 text-sm text-slate-500" colSpan={6}>
                      No historical runs yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>

    </div>
  )
}
