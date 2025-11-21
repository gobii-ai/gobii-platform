import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Beaker, Loader2, Play, RefreshCcw, CheckSquare } from 'lucide-react'

import {
  createSuiteRuns,
  fetchSuiteRuns,
  fetchSuites,
  type EvalSuite,
  type EvalSuiteRun,
} from '../api/evals'
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
      // Auto-select 'core' or 'all' or first suite if nothing selected
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

  const toggleAllSuites = () => {
    if (selectedSuites.size === suites.length) {
      setSelectedSuites(new Set())
    } else {
      setSelectedSuites(new Set(suites.map((s) => s.slug)))
    }
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
      <div className="card card--header">
        <div className="card__body card__body--header flex flex-col sm:flex-row sm:items-center justify-between gap-4 py-4 sm:py-3">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-white/90 rounded-xl shadow-sm text-blue-700">
              <Beaker className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Evals</h1>
              <p className="text-slate-600 mt-1.5">Run suites concurrently, watch progress, and inspect tasks.</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-200 rounded-lg shadow-sm hover:bg-slate-50 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-all"
              onClick={loadSuiteRuns}
              disabled={loadingRuns}
            >
              <RefreshCcw className={`w-4 h-4 ${loadingRuns ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <button
              type="button"
              className="inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-lg shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={handleLaunch}
              disabled={launching || (selectedSuites.size === 0 && suites.length > 0)}
            >
              {launching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              Launch
            </button>
          </div>
        </div>
      </div>

      {errorMessage && (
        <div className="rounded-lg bg-red-50 p-4 text-red-700 shadow-sm ring-1 ring-red-200">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 mt-0.5 shrink-0" />
            <div className="text-sm font-medium">{errorMessage}</div>
          </div>
        </div>
      )}

      <section className="card overflow-hidden" style={{ padding: 0 }}>
        <div className="bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between">
          <div>
            <h2 className="text-base font-bold text-slate-900 uppercase tracking-wide">Select Suites</h2>
            <p className="text-xs text-slate-500 mt-0.5">Choose which test suites to run against your agents.</p>
          </div>
          {suites.length > 0 && (
            <button
              type="button"
              onClick={toggleAllSuites}
              className="text-sm font-medium text-blue-600 hover:text-blue-700 hover:underline"
            >
              {selectedSuites.size === suites.length ? 'Deselect All' : 'Select All'}
            </button>
          )}
        </div>
        <div className="p-6">
          <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
            {suites.map((suite) => {
              const checked = selectedSuites.has(suite.slug)
              return (
                <div
                  key={suite.slug}
                  onClick={() => toggleSuiteSelection(suite.slug)}
                  className={`
                    group relative flex cursor-pointer flex-col gap-3 rounded-xl p-4 transition-all
                    ${checked 
                      ? 'bg-blue-50/50 shadow-md ring-2 ring-blue-500' 
                      : 'bg-white shadow-sm hover:shadow-md ring-1 ring-slate-200'
                    }
                  `}
                  role="checkbox"
                  aria-checked={checked}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      toggleSuiteSelection(suite.slug)
                    }
                  }}
                >
                  <div className="flex items-start justify-between gap-3">
                    <span className={`font-semibold text-base break-all ${checked ? 'text-blue-900' : 'text-slate-900'}`}>
                      {suite.slug}
                    </span>
                    <div className={`shrink-0 mt-0.5 transition-colors ${checked ? 'text-blue-600' : 'text-slate-300 group-hover:text-blue-400'}`}>
                      {checked 
                        ? <div className="bg-blue-600 text-white rounded-full p-0.5"><CheckSquare className="w-4 h-4" /></div>
                        : <div className="rounded-full w-5 h-5 border-2 border-slate-300 group-hover:border-blue-400" />
                      }
                    </div>
                  </div>
                  <p className={`text-sm line-clamp-3 leading-relaxed ${checked ? 'text-blue-800' : 'text-slate-600'}`}>
                    {suite.description || 'No description provided.'}
                  </p>
                  <div className="mt-auto pt-3 border-t border-dashed border-slate-200 flex items-center gap-2 text-xs uppercase tracking-wider text-slate-400 font-bold">
                     {pluralize(suite.scenario_slugs.length, 'scenario')}
                  </div>
                </div>
              )
            })}
            {!suites.length && (
              <div className="col-span-full py-12 text-center rounded-xl bg-white text-slate-500 shadow-sm ring-1 ring-slate-200">
                <p className="text-sm font-medium">No suites registered.</p>
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="card overflow-hidden" style={{ padding: 0 }}>
        <div className="bg-white border-b border-slate-200 px-6 py-4">
          <h2 className="text-base font-bold text-slate-900 uppercase tracking-wide">Recent Activity</h2>
        </div>

        <div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-white text-left text-xs uppercase tracking-wider text-slate-500 font-bold">
                <tr>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Suite</th>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Status</th>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Progress</th>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Started</th>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Duration</th>
                  <th className="px-6 py-4 bg-white border-b border-slate-100 text-right"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white">
                {suiteRuns.map((suite) => {
                  const duration = suite.finished_at && suite.started_at
                    ? Math.round((new Date(suite.finished_at).getTime() - new Date(suite.started_at).getTime()) / 1000) + 's'
                    : '—'
                    
                  return (
                    <tr key={suite.id} className="group hover:bg-slate-50 transition-colors">
                      <td className="px-6 py-4">
                        <div className="font-semibold text-slate-900">{suite.suite_slug}</div>
                        <div className="text-xs font-mono text-slate-400 mt-0.5">{suite.id.slice(0, 8)}</div>
                      </td>
                      <td className="px-6 py-4">
                        <StatusBadge status={suite.status || 'pending'} />
                      </td>
                      <td className="px-6 py-4 text-slate-700">
                         {suite.run_totals ? (
                           <div className="flex items-center gap-1.5 text-xs font-medium bg-slate-100 px-2 py-1 rounded-md w-fit">
                             <span className="text-slate-900">{suite.run_totals.completed}</span>
                             <span className="text-slate-400">/</span>
                             <span className="text-slate-600">{suite.run_totals.total_runs}</span>
                           </div>
                         ) : '—'}
                      </td>
                      <td className="px-6 py-4 text-slate-600 whitespace-nowrap">{formatTs(suite.started_at)}</td>
                      <td className="px-6 py-4 text-slate-600 font-mono text-xs">{duration}</td>
                      <td className="px-6 py-4 text-right">
                        <a
                          className="inline-flex items-center justify-center rounded-lg px-3 py-2 text-xs font-medium text-slate-700 ring-1 ring-slate-200 hover:bg-slate-100 transition-colors"
                          href={`/console/evals/${suite.id}/`}
                        >
                          View
                        </a>
                      </td>
                    </tr>
                  )
                })}
                {!suiteRuns.length && (
                  <tr>
                    <td className="px-6 py-12 text-sm text-slate-500 text-center" colSpan={6}>
                      No historical runs yet. Launch one above!
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