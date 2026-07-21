import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Beaker, CheckCircle2, Loader2, Play, RefreshCcw, Search } from 'lucide-react'
import { Button as AriaButton, Input, SearchField } from 'react-aria-components'

import {
  createSuiteRuns,
  fetchSuiteRuns,
  fetchSuites,
  type EvalScenario,
  type EvalSuite,
  type EvalSuiteRun,
} from '../api/evals'
import { fetchRoutingProfiles, type RoutingProfileListItem } from '../api/llmConfig'
import { EvalSelect, type EvalSelectOption } from '../components/evals/EvalSelect'
import {
  RecentActivityTable,
  ScenarioCatalogTable,
  SuiteSelectionTable,
  type EvalCatalogStatus,
} from '../components/evals/EvalTables'
import {
  EvalProfileSelector,
  type EvalProfileStatus,
} from '../components/evals/EvalProfileSelector'
import { EvalRunCountField } from '../components/evals/EvalRunCountField'

type RunTypeFilter = 'all' | EvalSuiteRun['run_type']
type LaunchResult = { source: 'suite' | 'scenario'; suiteRuns: EvalSuiteRun[] }
type LaunchError = { source: LaunchResult['source']; message: string }

const runTypeFilterOptions: { value: RunTypeFilter; label: string }[] = [
  { value: 'all', label: 'All runs' },
  { value: 'official', label: 'Official' },
  { value: 'one_off', label: 'One-off' },
]

const clampRunCount = (value: number) => Math.max(1, Math.min(10, value))

export function EvalsScreen() {
  const [suites, setSuites] = useState<EvalSuite[]>([])
  const [scenarios, setScenarios] = useState<EvalScenario[]>([])
  const [suiteRuns, setSuiteRuns] = useState<EvalSuiteRun[]>([])
  const [selectedSuites, setSelectedSuites] = useState<Set<string>>(new Set())
  const [runTypeFilter, setRunTypeFilter] = useState<RunTypeFilter>('all')
  const [suiteRunCount, setSuiteRunCount] = useState(1)
  const [catalogStatus, setCatalogStatus] = useState<EvalCatalogStatus>('loading')
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [launching, setLaunching] = useState(false)
  const [recentRunsError, setRecentRunsError] = useState<string | null>(null)
  const [launchError, setLaunchError] = useState<LaunchError | null>(null)
  const [launchResult, setLaunchResult] = useState<LaunchResult | null>(null)
  const [routingProfiles, setRoutingProfiles] = useState<RoutingProfileListItem[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState<string>('')
  const [profileStatus, setProfileStatus] = useState<EvalProfileStatus>('loading')
  const [scenarioQuery, setScenarioQuery] = useState('')
  const [scenarioTierFilter, setScenarioTierFilter] = useState('all')
  const [scenarioCategoryFilter, setScenarioCategoryFilter] = useState('all')
  const [scenarioCostFilter, setScenarioCostFilter] = useState('all')
  const [launchingScenarioSlug, setLaunchingScenarioSlug] = useState<string | null>(null)
  const suiteRunsRequestId = useRef(0)

  const loadSuites = useCallback(async () => {
    setCatalogStatus('loading')
    try {
      const result = await fetchSuites()
      setSuites(result.suites)
      setScenarios(result.scenarios || [])
      setCatalogStatus('ready')
    } catch (error) {
      console.error(error)
      setCatalogStatus('error')
    }
  }, [])

  const loadRoutingProfiles = useCallback(async () => {
    setProfileStatus('loading')
    try {
      const result = await fetchRoutingProfiles()
      setRoutingProfiles(result.profiles)
      setSelectedProfileId((current) => current || result.profiles.find((profile) => profile.is_active)?.id || '')
      setProfileStatus('ready')
    } catch (error) {
      console.error(error)
      setRoutingProfiles([])
      setSelectedProfileId('')
      setProfileStatus('error')
    }
  }, [])

  const loadSuiteRuns = useCallback(async () => {
    const requestId = ++suiteRunsRequestId.current
    setLoadingRuns(true)
    setRecentRunsError(null)
    try {
      const result = await fetchSuiteRuns({
        limit: 25,
        ...(runTypeFilter === 'all' ? {} : { run_type: runTypeFilter }),
      })
      if (requestId !== suiteRunsRequestId.current) return
      setSuiteRuns(result.suite_runs)
    } catch (error) {
      if (requestId !== suiteRunsRequestId.current) return
      console.error(error)
      setRecentRunsError('Unable to refresh recent activity right now.')
    } finally {
      if (requestId === suiteRunsRequestId.current) setLoadingRuns(false)
    }
  }, [runTypeFilter])

  useEffect(() => {
    void loadSuites()
    void loadRoutingProfiles()
  }, [loadRoutingProfiles, loadSuites])

  useEffect(() => {
    void loadSuiteRuns()
  }, [loadSuiteRuns])

  const profileOptions = useMemo<EvalSelectOption[]>(() => {
    if (profileStatus === 'loading') return [{ value: '', label: 'Loading profiles…' }]
    if (profileStatus === 'error') return [{ value: '', label: 'Default routing' }]
    return [
      { value: '', label: 'No profile' },
      ...routingProfiles.map((profile) => ({
        value: profile.id,
        label: `${profile.display_name || profile.name}${profile.is_active ? ' (active)' : ''}`,
      })),
    ]
  }, [profileStatus, routingProfiles])

  const selectedProfileLabel = useMemo(() => {
    if (profileStatus === 'loading') return 'Loading routing profiles…'
    if (profileStatus === 'error') return 'Default routing (profile list unavailable)'
    return profileOptions.find((option) => option.value === selectedProfileId)?.label || 'No profile'
  }, [profileOptions, profileStatus, selectedProfileId])

  const scenarioTierOptions = useMemo(
    () => buildFilterOptions('All tiers', scenarios.map((scenario) => scenario.metadata.tier)),
    [scenarios],
  )
  const scenarioCategoryOptions = useMemo(
    () => buildFilterOptions('All categories', scenarios.map((scenario) => scenario.metadata.category)),
    [scenarios],
  )
  const scenarioCostOptions = useMemo(
    () => buildFilterOptions('All costs', scenarios.map((scenario) => scenario.metadata.cost_class)),
    [scenarios],
  )

  const filteredScenarios = useMemo(() => {
    const query = scenarioQuery.trim().toLowerCase()
    return scenarios.filter((scenario) => {
      const metadata = scenario.metadata
      if (scenarioTierFilter !== 'all' && metadata.tier !== scenarioTierFilter) return false
      if (scenarioCategoryFilter !== 'all' && metadata.category !== scenarioCategoryFilter) return false
      if (scenarioCostFilter !== 'all' && metadata.cost_class !== scenarioCostFilter) return false
      if (!query) return true
      return [
        scenario.slug,
        scenario.description,
        metadata.category,
        metadata.tier,
        metadata.cost_class,
        metadata.expected_runtime,
        metadata.owner,
        metadata.area,
        ...metadata.tags,
        ...scenario.suite_slugs,
      ].join(' ').toLowerCase().includes(query)
    })
  }, [scenarios, scenarioQuery, scenarioTierFilter, scenarioCategoryFilter, scenarioCostFilter])

  const scenarioFilterKey = [scenarioQuery, scenarioTierFilter, scenarioCategoryFilter, scenarioCostFilter].join('|')

  const handleLaunch = async () => {
    if (selectedSuites.size === 0 || profileStatus === 'loading' || catalogStatus !== 'ready') return
    setLaunching(true)
    setLaunchError(null)
    setLaunchResult(null)
    try {
      const result = await createSuiteRuns({
        suite_slugs: Array.from(selectedSuites),
        agent_strategy: 'ephemeral_per_scenario',
        n_runs: clampRunCount(suiteRunCount),
        llm_routing_profile_id: selectedProfileId || null,
      })
      setLaunchResult({ source: 'suite', suiteRuns: result.suite_runs })
      await loadSuiteRuns()
    } catch (error) {
      console.error(error)
      setLaunchError({ source: 'suite', message: 'Failed to launch the selected suites. Review the configuration and try again.' })
    } finally {
      setLaunching(false)
    }
  }

  const handleScenarioLaunch = useCallback(async (scenario: EvalScenario) => {
    if (profileStatus === 'loading' || catalogStatus !== 'ready') return
    setLaunchingScenarioSlug(scenario.slug)
    setLaunchError(null)
    setLaunchResult(null)
    try {
      const result = await createSuiteRuns({
        scenario_slugs: [scenario.slug],
        agent_strategy: 'ephemeral_per_scenario',
        n_runs: 1,
        llm_routing_profile_id: selectedProfileId || null,
      })
      setLaunchResult({ source: 'scenario', suiteRuns: result.suite_runs })
      await loadSuiteRuns()
    } catch (error) {
      console.error(error)
      setLaunchError({ source: 'scenario', message: `Failed to launch ${scenario.slug}. Try again from its Run action.` })
    } finally {
      setLaunchingScenarioSlug(null)
    }
  }, [catalogStatus, loadSuiteRuns, profileStatus, selectedProfileId])

  return (
    <div className="app-shell">
      <div className="card card--header">
        <div className="card__body card__body--header flex items-center gap-4 py-4 sm:py-3">
          <div className="flex items-center gap-3">
            <div className="rounded-xl bg-white/90 p-2 text-blue-700 shadow-sm">
              <Beaker className="h-6 w-6" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-slate-900">Evals</h1>
              <p className="font-medium text-slate-600">Validate agent performance with concurrent test suites.</p>
            </div>
          </div>
        </div>
      </div>

      <section className="card overflow-hidden" style={{ padding: 0, gap: 0 }}>
        <div className="border-b border-blue-100 bg-blue-50/60 px-5 py-3.5">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-900">Select Suites</h2>
          <p className="mt-0.5 text-xs text-slate-500">Choose the suites and launch configuration.</p>
        </div>
        <div className="flex flex-wrap items-end gap-3 border-b border-blue-100 bg-white px-4 py-3">
          <EvalProfileSelector
            label="Routing profile for all launches"
            value={selectedProfileId}
            options={profileOptions}
            status={profileStatus}
            onChange={setSelectedProfileId}
            onRetry={() => void loadRoutingProfiles()}
          />
          <EvalRunCountField
            value={suiteRunCount}
            onChange={(value) => setSuiteRunCount(clampRunCount(value))}
          />
          <AriaButton
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-blue-600 bg-blue-600 px-4 text-sm font-semibold text-white transition-colors hover:border-blue-700 hover:bg-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/30 disabled:cursor-not-allowed disabled:opacity-50"
            onPress={() => void handleLaunch()}
            isDisabled={launching || selectedSuites.size === 0 || profileStatus === 'loading' || catalogStatus !== 'ready'}
          >
            {launching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 fill-current" />}
            {launching
              ? 'Launching…'
              : selectedSuites.size
                ? `Launch ${selectedSuites.size} ${selectedSuites.size === 1 ? 'suite' : 'suites'} · ${suiteRunCount} ${suiteRunCount === 1 ? 'run' : 'runs'}/scenario`
                : 'Select a suite'}
          </AriaButton>
        </div>
        {launchError?.source === 'suite' ? <SectionErrorNotice message={launchError.message} /> : null}
        {launchResult?.source === 'suite' ? <LaunchSuccessNotice suiteRuns={launchResult.suiteRuns} /> : null}
        <SuiteSelectionTable
          suites={suites}
          selectedSuites={selectedSuites}
          status={catalogStatus}
          onSelectionChange={setSelectedSuites}
          onRetry={() => void loadSuites()}
        />
      </section>

      <section className="card overflow-hidden" style={{ padding: 0, gap: 0 }}>
        <div className="border-b border-sky-100 bg-sky-50/60 px-5 py-3.5">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-900">Scenario Catalog</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Find and launch one focused eval. One-off runs use <span className="font-semibold text-slate-700">{selectedProfileLabel}</span>.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-b border-sky-100 bg-white px-4 py-3" role="group" aria-label="Scenario catalog filters">
          <SearchField
            aria-label="Search scenarios"
            value={scenarioQuery}
            onChange={setScenarioQuery}
            isDisabled={catalogStatus !== 'ready'}
            className="relative"
          >
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <Input
              placeholder="Search scenarios"
              className="h-10 w-60 rounded-lg border border-slate-300 bg-white pl-9 pr-3 text-sm text-slate-900 outline-none placeholder:text-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/25 disabled:cursor-not-allowed disabled:opacity-50"
            />
          </SearchField>
          <EvalSelect ariaLabel="Scenario tier" value={scenarioTierFilter} options={scenarioTierOptions} onChange={setScenarioTierFilter} disabled={catalogStatus !== 'ready'} />
          <EvalSelect ariaLabel="Scenario category" value={scenarioCategoryFilter} options={scenarioCategoryOptions} onChange={setScenarioCategoryFilter} disabled={catalogStatus !== 'ready'} />
          <EvalSelect ariaLabel="Scenario cost" value={scenarioCostFilter} options={scenarioCostOptions} onChange={setScenarioCostFilter} disabled={catalogStatus !== 'ready'} />
        </div>
        {launchError?.source === 'scenario' ? <SectionErrorNotice message={launchError.message} /> : null}
        {launchResult?.source === 'scenario' ? <LaunchSuccessNotice suiteRuns={launchResult.suiteRuns} /> : null}
        <ScenarioCatalogTable
          scenarios={filteredScenarios}
          filterKey={scenarioFilterKey}
          status={catalogStatus}
          launchingScenarioSlug={launchingScenarioSlug}
          launchDisabled={profileStatus === 'loading' || catalogStatus !== 'ready'}
          onLaunch={handleScenarioLaunch}
          onRetry={() => void loadSuites()}
        />
      </section>

      <section className="card overflow-hidden" style={{ padding: 0, gap: 0 }}>
        <div className="flex flex-col gap-3 border-b border-blue-100 bg-blue-50/60 px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-900">Recent Activity</h2>
          <div className="flex items-center gap-2">
            <div className="inline-flex items-center gap-1 rounded-lg border border-blue-100 bg-white p-1" role="group" aria-label="Filter recent eval activity">
              {runTypeFilterOptions.map((option) => (
                <AriaButton
                  key={option.value}
                  aria-pressed={runTypeFilter === option.value}
                  onPress={() => setRunTypeFilter(option.value)}
                  className={`rounded-md px-3 py-1 text-xs font-semibold transition-colors ${
                    runTypeFilter === option.value
                      ? 'bg-slate-900 text-white'
                      : 'text-slate-600 hover:bg-blue-50 hover:text-slate-900'
                  }`}
                >
                  {option.label}
                </AriaButton>
              ))}
            </div>
            <AriaButton
              className="rounded-lg border border-blue-100 bg-white p-2 text-slate-500 transition-colors hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700 disabled:opacity-50"
              onPress={() => void loadSuiteRuns()}
              isDisabled={loadingRuns}
              aria-label="Refresh recent activity"
            >
              <RefreshCcw className={`h-4 w-4 ${loadingRuns ? 'animate-spin' : ''}`} />
            </AriaButton>
          </div>
        </div>
        <RecentActivityTable
          suiteRuns={suiteRuns}
          loading={loadingRuns}
          error={recentRunsError}
          onRetry={() => void loadSuiteRuns()}
        />
      </section>
    </div>
  )
}

function buildFilterOptions(allLabel: string, values: string[]): EvalSelectOption[] {
  const uniqueValues = Array.from(new Set(values.filter(Boolean))).sort()
  return [
    { value: 'all', label: allLabel },
    ...uniqueValues.map((value) => ({ value, label: value })),
  ]
}

function LaunchSuccessNotice({ suiteRuns }: { suiteRuns: EvalSuiteRun[] }) {
  return (
    <div role="status" className="flex flex-col gap-2 border-b border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 sm:flex-row sm:items-center">
      <span className="inline-flex items-center gap-2 font-semibold">
        <CheckCircle2 className="h-4 w-4" />
        {suiteRuns.length === 1 ? 'Eval run created.' : `${suiteRuns.length} eval runs created.`}
      </span>
      <div className="flex flex-wrap items-center gap-2">
        {suiteRuns.map((suiteRun) => (
          <a
            key={suiteRun.id}
            href={`/evals/${suiteRun.id}/`}
            className="rounded-md bg-white px-2.5 py-1 text-xs font-bold text-emerald-800 ring-1 ring-emerald-200 hover:bg-emerald-100"
          >
            View {suiteRun.display_name || suiteRun.suite_slug}
          </a>
        ))}
      </div>
    </div>
  )
}

function SectionErrorNotice({ message }: { message: string }) {
  return (
    <div role="alert" className="flex items-start gap-2 border-b border-rose-200 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-800">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{message}</span>
    </div>
  )
}
