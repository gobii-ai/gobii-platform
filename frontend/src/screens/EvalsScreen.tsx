import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Beaker, Loader2, Play, RefreshCcw, CheckSquare, Square } from 'lucide-react'

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
              className="btn btn--secondary gap-2"
              onClick={loadSuiteRuns}
              disabled={loadingRuns}
            >
              <RefreshCcw className={`w-4 h-4 ${loadingRuns ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <button
              type="button"
              className="btn btn--primary gap-2"
              onClick={handleLaunch}
              disabled={launching || suites.length === 0}
            >
              {launching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              Launch
            </button>
          </div>
        </div>
      </header>

      {errorMessage && (
        <div className="card border-red-200 bg-red-50 text-red-700 mb-6">
          <div className="card__body flex items-start gap-2 text-sm">
            <AlertTriangle className="w-4 h-4 mt-0.5" />
            <div>{errorMessage}</div>
          </div>
        </div>
      )}

      <section className="card mb-6">
        <div className="card__body space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-slate-800">Select Suites</h2>
              <p className="text-xs text-slate-500">Choose which test suites to run.</p>
            </div>
            <button
              type="button"
              onClick={toggleAllSuites}
              className="text-sm text-blue-600 hover:text-blue-800 font-medium"
            >
              {selectedSuites.size === suites.length ? 'Deselect All' : 'Select All'}
            </button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4">
            {suites.map((suite) => {
              const checked = selectedSuites.has(suite.slug)
              return (
                <div
                  key={suite.slug}
                  onClick={() => toggleSuiteSelection(suite.slug)}
                  className={`
                    group relative flex cursor-pointer flex-col gap-2 rounded-lg border p-3 transition-all
                    ${checked 
                      ? 'border-blue-500 bg-blue-50 ring-1 ring-blue-500' 
                      : 'border-slate-200 bg-white hover:border-blue-300 hover:shadow-sm'
                    }
                  `}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={`font-medium ${checked ? 'text-blue-900' : 'text-slate-900'}`}>
                      {suite.slug}
                    </span>
                    {checked 
                      ? <CheckSquare className="w-4 h-4 text-blue-600" />
                      : <Square className="w-4 h-4 text-slate-300 group-hover:text-blue-400" />
                    }
                  </div>
                  <p className={`text-xs line-clamp-2 ${checked ? 'text-blue-700' : 'text-slate-600'}`}>
                    {suite.description || 'No description'}
                  </p>
                  <div className="mt-auto pt-2 flex items-center gap-2 text-[10px] uppercase tracking-wider text-slate-400 font-semibold">
                     {pluralize(suite.scenario_slugs.length, 'scenario')}
                  </div>
                </div>
              )
            })}
            {!suites.length && <div className="text-sm text-slate-500 col-span-full py-4 text-center">No suites registered.</div>}
          </div>
        </div>
      </section>

      <section className="card">
        <div className="card__body space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-800">Recent Suite Runs</h2>
          </div>

          <div className="overflow-hidden rounded-lg border border-slate-200">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 font-semibold">
                <tr>
                  <th className="px-4 py-3">Suite</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Progress</th>
                  <th className="px-4 py-3">Started</th>
                  <th className="px-4 py-3">Duration</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white">
                {suiteRuns.map((suite) => {
                  const duration = suite.finished_at && suite.started_at
                    ? Math.round((new Date(suite.finished_at).getTime() - new Date(suite.started_at).getTime()) / 1000) + 's'
                    : '—'
                    
                  return (
                    <tr key={suite.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-4 py-3 font-medium text-slate-900">
                        {suite.suite_slug}
                        <div className="text-xs text-slate-400 font-normal truncate max-w-[150px]">{suite.id.slice(0, 8)}...</div>
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={suite.status || 'pending'} />
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                         {suite.run_totals ? (
                           <div className="flex items-center gap-2">
                             <span className="font-medium text-slate-900">{suite.run_totals.completed}</span>
                             <span className="text-slate-400">/</span>
                             <span className="text-slate-500">{suite.run_totals.total_runs}</span>
                           </div>
                         ) : '—'}
                      </td>
                      <td className="px-4 py-3 text-slate-600 whitespace-nowrap">{formatTs(suite.started_at)}</td>
                      <td className="px-4 py-3 text-slate-600">{duration}</td>
                      <td className="px-4 py-3 text-right">
                        <a
                          className="inline-flex items-center justify-center rounded-md px-2.5 py-1.5 text-sm font-medium text-blue-600 hover:bg-blue-50 transition-colors"
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
                    <td className="px-4 py-8 text-sm text-slate-500 text-center" colSpan={6}>
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