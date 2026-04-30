import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Beaker, ChevronDown, Loader2, Play, RefreshCcw, CheckSquare, Minus, Plus } from 'lucide-react'

import {
  createGlobalSkillEvalRun,
  createSuiteRuns,
  fetchGlobalSkillEvalLauncher,
  fetchSuiteRuns,
  fetchSuites,
  type EvalSuite,
  type EvalSuiteRun,
  type GlobalSkillEvalSkill,
} from '../api/evals'
import { fetchRoutingProfiles, type RoutingProfileListItem } from '../api/llmConfig'
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
const formatPassRate = (taskTotals: EvalSuiteRun['task_totals'] | null | undefined) => {
  if (!taskTotals || taskTotals.pass_rate == null) return '—'
  return `${Math.round(taskTotals.pass_rate * 100)}%`
}

export function EvalsScreen() {
  const [suites, setSuites] = useState<EvalSuite[]>([])
  const [suiteRuns, setSuiteRuns] = useState<EvalSuiteRun[]>([])
  const [globalSkills, setGlobalSkills] = useState<GlobalSkillEvalSkill[]>([])
  const [globalSecretsUrl, setGlobalSecretsUrl] = useState<string>('/console/secrets/')
  const [rubricVersion, setRubricVersion] = useState<string>('v1')
  const [selectedSuites, setSelectedSuites] = useState<Set<string>>(new Set())
  const [selectedGlobalSkillId, setSelectedGlobalSkillId] = useState<string>('')
  const [globalSkillTaskPrompt, setGlobalSkillTaskPrompt] = useState<string>('')
  const [runTypeFilter, setRunTypeFilter] = useState<'all' | EvalSuiteRun['run_type']>('all')
  const [suiteRunCount, setSuiteRunCount] = useState<number>(3)
  const [skillEvalRunCount, setSkillEvalRunCount] = useState<number>(1)
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [launching, setLaunching] = useState(false)
  const [launchingGlobalSkillEval, setLaunchingGlobalSkillEval] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [routingProfiles, setRoutingProfiles] = useState<RoutingProfileListItem[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null)

  const listRefreshInFlight = useRef(false)
  const runTypeFilterOptions: { value: 'all' | EvalSuiteRun['run_type']; label: string }[] = [
    { value: 'all', label: 'All runs' },
    { value: 'official', label: 'Official' },
    { value: 'one_off', label: 'One-off' },
  ]
  const clampRunCount = useCallback((value: number) => Math.max(1, Math.min(10, value)), [])

  const loadSuites = useCallback(async () => {
    try {
      const result = await fetchSuites()
      setSuites(result.suites)
    } catch (error) {
      console.error(error)
      setErrorMessage('Unable to load suites right now.')
    }
  }, [selectedSuites.size])

  const loadGlobalSkills = useCallback(async () => {
    try {
      const result = await fetchGlobalSkillEvalLauncher()
      setGlobalSkills(result.global_skills)
      setGlobalSecretsUrl(result.global_secrets_url)
      setRubricVersion(result.rubric_version)
      setSelectedGlobalSkillId((prev) => {
        if (prev && result.global_skills.some((skill) => skill.id === prev)) return prev
        return result.global_skills[0]?.id || ''
      })
    } catch (error) {
      console.error(error)
      setErrorMessage('Unable to load global skill eval options right now.')
    }
  }, [])

  const loadRoutingProfiles = useCallback(async () => {
    try {
      const result = await fetchRoutingProfiles()
      setRoutingProfiles(result.profiles)
      // Default to the active profile
      const activeProfile = result.profiles.find((p) => p.is_active)
      if (activeProfile && !selectedProfileId) {
        setSelectedProfileId(activeProfile.id)
      }
    } catch (error) {
      console.error(error)
      // Non-fatal - profiles are optional
    }
  }, [selectedProfileId])

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
    loadGlobalSkills()
    loadSuiteRuns()
    loadRoutingProfiles()
  }, [loadSuites, loadGlobalSkills, loadSuiteRuns, loadRoutingProfiles])

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

  const selectedGlobalSkill = globalSkills.find((skill) => skill.id === selectedGlobalSkillId) || null
  const hasMissingGlobalSkillSecrets = Boolean(selectedGlobalSkill?.missing_required_secrets.length)

  const handleLaunch = async () => {
    setLaunching(true)
    setErrorMessage(null)
    try {
      const suite_slugs = selectedSuites.size ? Array.from(selectedSuites) : ['all']
      await createSuiteRuns({
        suite_slugs,
        agent_strategy: 'ephemeral_per_scenario',
        n_runs: clampRunCount(suiteRunCount),
        llm_routing_profile_id: selectedProfileId,
      })
      await loadSuiteRuns()
    } catch (error) {
      console.error(error)
      setErrorMessage('Failed to launch evals.')
    } finally {
      setLaunching(false)
    }
  }

  const handleGlobalSkillEvalLaunch = async () => {
    if (!selectedGlobalSkill) return
    setLaunchingGlobalSkillEval(true)
    setErrorMessage(null)
    try {
      await createGlobalSkillEvalRun({
        global_skill_id: selectedGlobalSkill.id,
        task_prompt: globalSkillTaskPrompt.trim(),
        n_runs: clampRunCount(skillEvalRunCount),
        llm_routing_profile_id: selectedProfileId,
      })
      await loadSuiteRuns()
      await loadGlobalSkills()
    } catch (error) {
      console.error(error)
      setErrorMessage('Failed to launch global skill eval.')
    } finally {
      setLaunchingGlobalSkillEval(false)
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
              <p className="text-slate-600 font-medium">
                Validate agent performance with concurrent test suites.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="p-2 text-slate-400 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
              onClick={loadSuiteRuns}
              disabled={loadingRuns}
              title="Refresh list"
            >
              <RefreshCcw className={`w-5 h-5 ${loadingRuns ? 'animate-spin' : ''}`} />
            </button>

            <div className="h-6 w-px bg-slate-200 mx-1" />

            {routingProfiles.length > 0 && (
              <div className="relative">
                <select
                  value={selectedProfileId || ''}
                  onChange={(e) => setSelectedProfileId(e.target.value || null)}
                  className="appearance-none bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 pr-8 text-xs font-semibold text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer"
                >
                  <option value="">No profile</option>
                  {routingProfiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.display_name || profile.name}
                      {profile.is_active ? ' (active)' : ''}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-500 pointer-events-none" />
              </div>
            )}

            <div className="flex items-center gap-1.5 p-1 bg-slate-100 rounded-lg border border-slate-200">
              <span className="text-xs font-bold text-slate-500 uppercase tracking-wider px-2">Runs</span>
              <button
                type="button"
                className="w-6 h-6 flex items-center justify-center rounded bg-white text-slate-600 shadow-sm hover:text-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
                onClick={() => setSuiteRunCount((prev) => clampRunCount(prev - 1))}
                disabled={suiteRunCount <= 1}
              >
                <Minus className="w-3 h-3" strokeWidth={3} />
              </button>
              <div className="w-6 text-center text-sm font-bold text-slate-700 tabular-nums">
                {suiteRunCount}
              </div>
              <button
                type="button"
                className="w-6 h-6 flex items-center justify-center rounded bg-white text-slate-600 shadow-sm hover:text-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
                onClick={() => setSuiteRunCount((prev) => clampRunCount(prev + 1))}
                disabled={suiteRunCount >= 10}
              >
                <Plus className="w-3 h-3" strokeWidth={3} />
              </button>
            </div>

            <button
              type="button"
              className="inline-flex items-center justify-center gap-2 px-4 py-2 text-sm font-bold text-white bg-blue-600 rounded-lg shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={handleLaunch}
              disabled={launching || (selectedSuites.size === 0 && suites.length > 0)}
            >
              {launching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-current" />}
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
        <div className="bg-gradient-to-r from-emerald-50/90 to-cyan-50/90 border-b border-emerald-100 px-6 py-4">
          <h2 className="text-base font-bold text-slate-900 uppercase tracking-wide">Global Skill Eval</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Launch an ad hoc eval that asks an ephemeral agent to enable and use one global skill.
          </p>
        </div>
        <div className="grid gap-6 p-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-xs font-bold uppercase tracking-wider text-slate-500">Global Skill</label>
              <div className="relative">
                <select
                  value={selectedGlobalSkillId}
                  onChange={(e) => setSelectedGlobalSkillId(e.target.value)}
                  className="w-full appearance-none rounded-xl border border-slate-200 bg-white px-4 py-3 pr-10 text-sm font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                >
                  {globalSkills.map((skill) => (
                    <option key={skill.id} value={skill.id}>
                      {skill.name}
                    </option>
                  ))}
                  {!globalSkills.length && <option value="">No active global skills</option>}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500 pointer-events-none" />
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-xs font-bold uppercase tracking-wider text-slate-500">Task Prompt</label>
              <textarea
                value={globalSkillTaskPrompt}
                onChange={(e) => setGlobalSkillTaskPrompt(e.target.value)}
                rows={5}
                placeholder="Ask the agent to complete a task that should require this skill."
                className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <div className="inline-flex items-center rounded-lg bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-200">
                Judge rubric: built-in {rubricVersion}
              </div>
              <div className="flex items-center gap-1.5 rounded-lg bg-white px-2 py-1.5 text-xs font-semibold text-slate-600 ring-1 ring-slate-200">
                <span className="px-1 text-slate-500 uppercase tracking-wider">Runs</span>
                <button
                  type="button"
                  className="flex h-6 w-6 items-center justify-center rounded bg-slate-100 text-slate-600 shadow-sm hover:text-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-500 disabled:opacity-50"
                  onClick={() => setSkillEvalRunCount((prev) => clampRunCount(prev - 1))}
                  disabled={skillEvalRunCount <= 1}
                >
                  <Minus className="h-3 w-3" strokeWidth={3} />
                </button>
                <div className="w-6 text-center text-sm font-bold tabular-nums text-slate-800">
                  {skillEvalRunCount}
                </div>
                <button
                  type="button"
                  className="flex h-6 w-6 items-center justify-center rounded bg-slate-100 text-slate-600 shadow-sm hover:text-emerald-600 focus:outline-none focus:ring-1 focus:ring-emerald-500 disabled:opacity-50"
                  onClick={() => setSkillEvalRunCount((prev) => clampRunCount(prev + 1))}
                  disabled={skillEvalRunCount >= 10}
                >
                  <Plus className="h-3 w-3" strokeWidth={3} />
                </button>
              </div>
              <button
                type="button"
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-bold text-white shadow-sm transition-all hover:bg-emerald-700 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
                onClick={handleGlobalSkillEvalLaunch}
                disabled={
                  launchingGlobalSkillEval
                  || !selectedGlobalSkill
                  || !globalSkillTaskPrompt.trim()
                  || hasMissingGlobalSkillSecrets
                }
              >
                {launchingGlobalSkillEval ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 fill-current" />}
                Launch Skill Eval
              </button>
            </div>
          </div>

          <div className="rounded-2xl bg-slate-900 px-5 py-5 text-slate-100">
            {selectedGlobalSkill ? (
              <div className="space-y-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-lg font-bold tracking-tight">{selectedGlobalSkill.name}</span>
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider ${
                        selectedGlobalSkill.launchable
                          ? 'bg-emerald-400/15 text-emerald-200 ring-1 ring-emerald-300/30'
                          : 'bg-amber-400/15 text-amber-200 ring-1 ring-amber-300/30'
                      }`}
                    >
                      {selectedGlobalSkill.launchable ? 'Launchable' : 'Needs Secrets'}
                    </span>
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-slate-300">
                    {selectedGlobalSkill.description || 'No description provided.'}
                  </p>
                </div>

                <div>
                  <p className="text-[11px] font-bold uppercase tracking-[0.2em] text-slate-400">Effective Tools</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {selectedGlobalSkill.effective_tool_ids.length ? selectedGlobalSkill.effective_tool_ids.map((toolId) => (
                      <span key={toolId} className="inline-flex items-center rounded-full bg-white/10 px-2.5 py-1 text-xs font-medium text-slate-100 ring-1 ring-white/10">
                        {toolId}
                      </span>
                    )) : (
                      <span className="text-sm text-slate-400">No effective tools recorded.</span>
                    )}
                  </div>
                </div>

                <div>
                  <p className="text-[11px] font-bold uppercase tracking-[0.2em] text-slate-400">Required Secrets</p>
                  <div className="mt-2 space-y-2">
                    {selectedGlobalSkill.required_secret_status.length ? selectedGlobalSkill.required_secret_status.map((secret) => (
                      <div key={secret.label} className="rounded-xl bg-white/5 px-3 py-3 ring-1 ring-white/10">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="text-sm font-semibold text-white">{secret.name}</div>
                            <div className="mt-1 text-xs text-slate-400">{secret.label}</div>
                          </div>
                          <span
                            className={`inline-flex items-center rounded-full px-2 py-1 text-[11px] font-bold uppercase tracking-wide ${
                              secret.status === 'available'
                                ? 'bg-emerald-400/15 text-emerald-200'
                                : 'bg-amber-400/15 text-amber-200'
                            }`}
                          >
                            {secret.status === 'available' ? 'Ready' : 'Missing'}
                          </span>
                        </div>
                        {secret.description ? (
                          <p className="mt-2 text-xs leading-relaxed text-slate-300">{secret.description}</p>
                        ) : null}
                      </div>
                    )) : (
                      <div className="rounded-xl bg-emerald-400/10 px-3 py-3 text-sm text-emerald-100 ring-1 ring-emerald-300/20">
                        No secrets required.
                      </div>
                    )}
                  </div>
                </div>

                {hasMissingGlobalSkillSecrets ? (
                  <div className="rounded-xl bg-amber-400/10 px-4 py-3 text-sm text-amber-100 ring-1 ring-amber-300/20">
                    This eval is blocked until the required global secrets exist.
                    <a href={globalSecretsUrl} className="ml-2 font-semibold text-white underline underline-offset-4">
                      Manage global secrets
                    </a>
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="text-sm text-slate-300">No active global skills are available for skill evals.</div>
            )}
          </div>
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
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Eval</th>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Type</th>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Status</th>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Progress</th>
                  <th className="px-6 py-4 bg-white border-b border-slate-100">Avg Pass</th>
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
                        <div className="flex items-center gap-2">
                          <div className="font-semibold text-slate-900">{suite.display_name || suite.suite_slug}</div>
                          {suite.launcher_type === 'global_skill' ? (
                            <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-emerald-700 ring-1 ring-emerald-200">
                              Skill Eval
                            </span>
                          ) : null}
                        </div>
                        {suite.launcher_type === 'global_skill' && suite.skill_eval?.task_prompt ? (
                          <div className="mt-1 max-w-xl text-xs leading-relaxed text-slate-500">
                            {suite.skill_eval.task_prompt}
                          </div>
                        ) : null}
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
                      <td className="px-6 py-4 text-slate-700">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-slate-900">{formatPassRate(suite.task_totals || null)}</span>
                          {suite.task_totals ? (
                            <span className="text-xs text-slate-500">
                              {(suite.task_totals.passed ?? 0)}/{suite.task_totals.completed ?? suite.task_totals.total}
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="px-6 py-4 text-slate-600 whitespace-nowrap">{formatTs(suite.started_at)}</td>
                      <td className="px-6 py-4 text-slate-600 font-mono text-xs">{duration}</td>
                      <td className="px-6 py-4 text-right">
                        <a
                          className="inline-flex items-center justify-center rounded-lg px-3 py-2 text-xs font-medium text-slate-700 ring-1 ring-slate-200 hover:bg-slate-100 transition-colors"
                          href={`/evals/${suite.id}/`}
                        >
                          View
                        </a>
                      </td>
                    </tr>
                  )
                })}
                {!suiteRuns.length && (
                  <tr>
                    <td className="px-6 py-12 text-sm text-slate-500 text-center" colSpan={8}>
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
