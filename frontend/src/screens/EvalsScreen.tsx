import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Beaker, Loader2, Play, RefreshCcw, CheckSquare } from 'lucide-react'

import {
  createSuiteRuns,
  fetchSuiteRuns,
  fetchSuites,
  type EvalRunType,
  type EvalSuite,
  type EvalSuiteRun,
} from '../api/evals'
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

const pluralize = (count: number, word: string) => `${count} ${count === 1 ? word : `${word}s`}`

export function EvalsScreen() {
  const [suites, setSuites] = useState<EvalSuite[]>([])
  const [suiteRuns, setSuiteRuns] = useState<EvalSuiteRun[]>([])
  const [selectedSuites, setSelectedSuites] = useState<Set<string>>(new Set())
  const [runType, setRunType] = useState<EvalRunType>('one_off')
  const [runTypeFilter, setRunTypeFilter] = useState<'all' | EvalRunType>('all')
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [launching, setLaunching] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const listRefreshInFlight = useRef(false)
  const runTypeFilterOptions: { value: 'all' | EvalRunType; label: string }[] = [
    { value: 'all', label: 'All runs' },
    { value: 'official', label: 'Official' },
    { value: 'one_off', label: 'One-off' },
  ]

  const loadSuites = useCallback(async () => {
    try {
      const result = await fetchSuites()
      setSuites(result.suites)
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
      const result = await fetchSuiteRuns({
        limit: 25,
        ...(runTypeFilter === 'all' ? {} : { run_type: runTypeFilter }),
      })
      setSuiteRuns(result.suite_runs)
    } catch (error) {
      console.error(error)
      setErrorMessage('Unable to load suite runs right now.')
    } finally {
      setLoadingRuns(false)
      listRefreshInFlight.current = false
    }
  }, [runTypeFilter])

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
      await createSuiteRuns({
        suite_slugs,
        agent_strategy: 'ephemeral_per_scenario',
        run_type: runType,
        official: runType === 'official',
      })
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
        <div className="px-4 pb-4 sm:px-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Run mode</span>
              <div className="inline-flex rounded-lg bg-slate-100 p-1 shadow-inner ring-1 ring-slate-200/80">
                <button
                  type="button"
                  onClick={() => setRunType('one_off')}
                  className={`
                    px-3 py-1.5 text-xs font-semibold rounded-md transition-all
                    ${runType === 'one_off'
                      ? 'bg-white text-blue-700 shadow-sm ring-1 ring-slate-200'
                      : 'text-slate-600 hover:text-slate-900'
                    }
                  `}
                >
                  One-off
                </button>
                <button
                  type="button"
                  onClick={() => setRunType('official')}
                  className={`
                    px-3 py-1.5 text-xs font-semibold rounded-md transition-all
                    ${runType === 'official'
                      ? 'bg-emerald-50 text-emerald-700 shadow-sm ring-1 ring-emerald-200'
                      : 'text-slate-600 hover:text-slate-900'
                    }
                  `}
                >
                  Official
                </button>
              </div>
            </div>
            <p className="text-xs leading-relaxed text-slate-600 sm:text-right">
              One-off runs are for ad-hoc checks. Mark runs as official to keep them in long-term performance and cost tracking.
            </p>
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
        <div className="bg-gradient-to-r from-blue-50/80 to-indigo-50/80 border-b border-blue-100 px-6 py-4 flex items-center justify-between">
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
        <div className="divide-y divide-slate-100">
          {suites.map((suite) => {
            const checked = selectedSuites.has(suite.slug)
            return (
              <div
                key={suite.slug}
                onClick={() => toggleSuiteSelection(suite.slug)}
                className="flex items-start gap-4 p-6 cursor-pointer group bg-white"
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
                <div className="pt-1">
                  <div 
                    className={`
                      w-5 h-5 rounded border flex items-center justify-center transition-all
                      ${checked 
                        ? 'bg-blue-600 border-blue-600 text-white' 
                        : 'bg-white border-slate-300 text-transparent group-hover:border-blue-400'
                      }
                    `}
                  >
                    <CheckSquare className="w-3.5 h-3.5" strokeWidth={3} />
                  </div>
                </div>
                
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3 mb-1">
                    <span className={`text-sm font-bold ${checked ? 'text-blue-900' : 'text-slate-900'}`}>
                      {suite.slug}
                    </span>
                    <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                      {pluralize(suite.scenario_slugs.length, 'scenario')}
                    </span>
                  </div>
                  <p className="text-sm text-slate-500 leading-relaxed max-w-3xl">
                    {suite.description || 'No description provided.'}
                  </p>
                </div>
              </div>
            )
          })}
          {!suites.length && (
            <div className="p-12 text-center text-slate-500">
              <p className="text-sm font-medium">No suites registered.</p>
            </div>
          )}
        </div>
      </section>

      <section className="card overflow-hidden" style={{ padding: 0 }}>
        <div className="bg-gradient-to-r from-blue-50/80 to-indigo-50/80 border-b border-blue-100 px-6 py-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-base font-bold text-slate-900 uppercase tracking-wide">Recent Activity</h2>
          <div className="inline-flex items-center gap-1 rounded-lg bg-white/70 p-1 ring-1 ring-slate-200 shadow-sm">
            {runTypeFilterOptions.map((option) => {
              const active = runTypeFilter === option.value
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setRunTypeFilter(option.value)}
                  className={`
                    px-3 py-1 text-xs font-semibold rounded-md transition-all
                    ${active
                      ? 'bg-slate-900 text-white shadow-sm ring-1 ring-slate-900/10'
                      : 'text-slate-600 hover:text-slate-900'
                    }
                  `}
                >
                  {option.label}
                </button>
              )
            })}
          </div>
        </div>

        <div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-white text-left text-xs uppercase tracking-wider text-slate-500 font-bold">
                <tr>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Suite</th>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Type</th>
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
                    <tr key={suite.id} className="group">
                      <td className="px-6 py-4">
                        <div className="font-semibold text-slate-900">{suite.suite_slug}</div>
                        <div className="text-xs font-mono text-slate-400 mt-0.5">{suite.id.slice(0, 8)}</div>
                      </td>
                      <td className="px-6 py-4">
                        <RunTypeBadge runType={suite.run_type} />
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
                    <td className="px-6 py-12 text-sm text-slate-500 text-center" colSpan={7}>
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
