import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Switch as AriaSwitch } from 'react-aria-components'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, Info, RefreshCw, XCircle } from 'lucide-react'

import { fetchSystemSettings, updateSystemSetting, type SystemSetting } from '../api/systemSettings'
import { HttpError } from '../api/http'

type RowStatusMap = Record<string, { error?: string | null }>

const sourceLabels: Record<SystemSetting['source'], string> = {
  database: 'Overridden',
  env: 'Environment',
  default: 'Default',
}

const sourceBadgeStyles: Record<SystemSetting['source'], string> = {
  database: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  env: 'border-blue-200 bg-blue-50 text-blue-800',
  default: 'border-sky-200 bg-sky-50 text-sky-800',
}

const buttonStyles = {
  ghost:
    'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500/30 disabled:opacity-60 disabled:cursor-not-allowed',
  reset:
    'inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500/30 disabled:opacity-60 disabled:cursor-not-allowed',
}

const formatValue = (setting: SystemSetting, value: number | boolean | null) => {
  if (value === null || value === undefined) {
    return '—'
  }
  if (setting.value_type === 'bool') {
    return value ? 'Enabled' : 'Disabled'
  }
  if (setting.disable_value !== null && setting.disable_value !== undefined && value === setting.disable_value) {
    return `Disabled (${value})`
  }
  if (setting.unit) {
    return `${value} ${setting.unit}`
  }
  return String(value)
}

const draftFromSetting = (setting: SystemSetting) =>
  setting.db_value !== null && setting.db_value !== undefined ? String(setting.db_value) : ''

const toCategoryId = (category: string) =>
  `system-settings-${category.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')}`

export function SystemSettingsScreen() {
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => ['system-settings'] as const, [])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [dirtyKeys, setDirtyKeys] = useState<Record<string, boolean>>({})
  const [rowStatus, setRowStatus] = useState<RowStatusMap>({})
  const [banner, setBanner] = useState<string | null>(null)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [activeCategoryId, setActiveCategoryId] = useState<string | null>(null)
  const activeCategoryRef = useRef<string | null>(null)
  const visibilityRef = useRef(new Map<string, number>())

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey,
    queryFn: ({ signal }) => fetchSystemSettings(signal),
  })

  useEffect(() => {
    if (!data?.settings) {
      return
    }
    setDrafts((prev) => {
      const next: Record<string, string> = { ...prev }
      data.settings.forEach((setting) => {
        const shouldReset = !dirtyKeys[setting.key] || !(setting.key in prev)
        if (shouldReset) {
          next[setting.key] = draftFromSetting(setting)
        }
      })
      return next
    })
  }, [data, dirtyKeys])

  const settings = data?.settings ?? []
  const listError = error instanceof Error ? error.message : null

  const updateRowError = useCallback((key: string, error: string | null) => {
    setRowStatus((prev) => ({
      ...prev,
      [key]: { error },
    }))
  }, [])

  const hasChanges = useMemo(
    () => settings.some((setting) => (drafts[setting.key] ?? '') !== draftFromSetting(setting)),
    [drafts, settings],
  )

  const categories = useMemo(() => {
    const map = new Map<string, SystemSetting[]>()
    const order: string[] = []
    settings.forEach((setting) => {
      const category = setting.category || 'Other'
      if (!map.has(category)) {
        map.set(category, [])
        order.push(category)
      }
      map.get(category)?.push(setting)
    })
    return order.map((category) => ({
      name: category,
      id: toCategoryId(category),
      settings: map.get(category) ?? [],
    }))
  }, [settings])

  const setActiveCategory = useCallback((nextId: string) => {
    activeCategoryRef.current = nextId
    setActiveCategoryId(nextId)
  }, [])

  useEffect(() => {
    if (!categories.length) {
      return
    }
    if (!activeCategoryRef.current) {
      setActiveCategory(categories[0].id)
    }
    const sections = Array.from(document.querySelectorAll<HTMLElement>('[data-category-id]'))
    if (!sections.length) {
      return
    }
    visibilityRef.current.clear()
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const id = entry.target.getAttribute('data-category-id')
          if (!id) {
            return
          }
          if (entry.isIntersecting) {
            visibilityRef.current.set(id, entry.intersectionRatio)
          } else {
            visibilityRef.current.delete(id)
          }
        })
        if (!visibilityRef.current.size) {
          return
        }
        let bestId = activeCategoryRef.current
        let bestRatio = -1
        visibilityRef.current.forEach((ratio, id) => {
          if (ratio > bestRatio) {
            bestRatio = ratio
            bestId = id
          }
        })
        if (bestId && bestId !== activeCategoryRef.current) {
          setActiveCategory(bestId)
        }
      },
      {
        rootMargin: '0px 0px -60% 0px',
        threshold: [0, 0.15, 0.3, 0.6, 1],
      },
    )
    sections.forEach((section) => observer.observe(section))
    return () => {
      observer.disconnect()
      visibilityRef.current.clear()
    }
  }, [categories, setActiveCategory])

  const resetAllDrafts = useCallback(
    (nextSettings: SystemSetting[]) => {
      const nextDrafts: Record<string, string> = {}
      nextSettings.forEach((setting) => {
        nextDrafts[setting.key] = draftFromSetting(setting)
      })
      setDrafts(nextDrafts)
      setDirtyKeys({})
      setRowStatus({})
      setSaveError(null)
      setErrorBanner(null)
    },
    [setDrafts],
  )

  const handleCancelAll = useCallback(() => {
    if (!data?.settings) {
      return
    }
    resetAllDrafts(data.settings)
  }, [data, resetAllDrafts])

  const handleSaveAll = useCallback(async () => {
    if (!settings.length) {
      return
    }
    const changes = settings.filter(
      (setting) => (drafts[setting.key] ?? '') !== draftFromSetting(setting),
    )
    if (!changes.length) {
      return
    }
    setSaving(true)
    setSaveError(null)
    setErrorBanner(null)
    let firstError: string | null = null
    for (const setting of changes) {
      const draftValue = (drafts[setting.key] ?? '').trim()
      try {
        const payloadValue =
          draftValue && setting.value_type === 'bool' ? draftValue === 'true' : draftValue
        const response = await updateSystemSetting(
          setting.key,
          draftValue ? { value: payloadValue } : { clear: true },
        )
        updateRowError(setting.key, null)
        setDrafts((prev) => ({
          ...prev,
          [setting.key]: draftFromSetting(response.setting),
        }))
        setDirtyKeys((prev) => ({
          ...prev,
          [setting.key]: false,
        }))
      } catch (err) {
        const message =
          err instanceof HttpError
            ? (typeof err.body === 'string' ? err.body : err.statusText)
            : err instanceof Error
              ? err.message
              : 'Failed to update setting.'
        updateRowError(setting.key, message)
        if (!firstError) {
          firstError = message
        }
      }
    }
    if (firstError) {
      setSaveError(firstError)
      setErrorBanner(firstError)
    } else {
      setBanner('System settings saved.')
    }
    setSaving(false)
    queryClient.invalidateQueries({ queryKey })
  }, [drafts, queryClient, queryKey, settings, updateRowError])

  return (
    <div className="space-y-4">
      {banner && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">
          {banner}
        </div>
      )}
      {errorBanner && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-800">
          {errorBanner}
        </div>
      )}
      <div className="space-y-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">System settings</h1>
            <p className="text-sm text-slate-600">Configure system-level overrides.</p>
          </div>
          <button
            type="button"
            className={buttonStyles.ghost}
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            {isFetching ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        {listError && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
            Failed to load settings. {listError}
          </div>
        )}

        <div className="grid gap-8 lg:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="flex flex-col gap-3 lg:sticky lg:top-24 lg:self-start">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Categories</p>
            <nav className="flex gap-2 overflow-x-auto pb-2 lg:flex-col lg:overflow-visible lg:pb-0">
              {categories.map((category) => (
                <a
                  key={category.id}
                  href={`#${category.id}`}
                  onClick={() => setActiveCategory(category.id)}
                  aria-current={activeCategoryId === category.id ? 'page' : undefined}
                  className={
                    activeCategoryId === category.id
                      ? 'inline-flex items-center rounded-full border border-blue-500 bg-blue-50 px-3 py-1.5 text-xs font-semibold text-blue-700 shadow-[0_10px_25px_rgba(37,99,235,0.15)] transition lg:rounded-lg'
                      : 'inline-flex items-center rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900 lg:rounded-lg'
                  }
                >
                  {category.name}
                </a>
              ))}
            </nav>
          </aside>
          <div className="space-y-10">
            {isLoading ? (
              <div className="py-6 text-sm text-slate-600">Loading settings…</div>
            ) : (
              categories.map((category) => (
                <section
                  key={category.id}
                  id={category.id}
                  data-category-id={category.id}
                  className="space-y-4 scroll-mt-28"
                >
                  <div>
                    <h2 className="text-lg font-semibold text-slate-900">{category.name}</h2>
                    <p className="text-xs text-slate-500">Manage {category.name.toLowerCase()} settings.</p>
                  </div>
                  <div className="space-y-6">
                    {category.settings.map((setting) => {
                      const draftValue = drafts[setting.key] ?? ''
                      const hasOverride = setting.db_value !== null && setting.db_value !== undefined
                      const status = rowStatus[setting.key]
                      const isBool = setting.value_type === 'bool'
                      const boolValue =
                        draftValue.trim() !== '' ? draftValue === 'true' : Boolean(setting.effective_value)
                      const minValue = setting.min_value ?? undefined
                      const placeholderValue =
                        hasOverride && draftValue.trim() === '' ? setting.fallback_value : setting.effective_value
                      return (
                        <div key={setting.key} className="rounded-2xl border border-slate-200 bg-white px-5 py-5">
                          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
                            <div className="space-y-2">
                              <div className="flex flex-wrap items-center gap-2">
                                <h3 className="text-base font-semibold text-slate-900">{setting.label}</h3>
                                <span
                                  className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${sourceBadgeStyles[setting.source]}`}
                                >
                                  {sourceLabels[setting.source]}
                                </span>
                              </div>
                              <p className="text-sm text-slate-600">{setting.description}</p>
                              <p className="text-xs text-slate-500">
                                Effective value: {formatValue(setting, setting.effective_value)} · Env var: {setting.env_var}{' '}
                                {setting.env_set ? '(set)' : '(not set)'}
                              </p>
                            </div>
                            <div className="space-y-3">
                              <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                                Override value
                              </label>
                              {isBool ? (
                                <div className="flex items-center">
                                  <AriaSwitch
                                    aria-label={`${setting.label} toggle`}
                                    isSelected={boolValue}
                                    onChange={(isSelected) => {
                                      setDrafts((prev) => ({
                                        ...prev,
                                        [setting.key]: isSelected ? 'true' : 'false',
                                      }))
                                      setDirtyKeys((prev) => ({
                                        ...prev,
                                        [setting.key]: true,
                                      }))
                                      if (status?.error) {
                                        updateRowError(setting.key, null)
                                      }
                                    }}
                                    className="relative inline-flex h-6 w-11 cursor-pointer items-center focus:outline-none"
                                  >
                                    {({ isSelected, isFocusVisible }) => (
                                      <>
                                        <span
                                          aria-hidden="true"
                                          className={`h-6 w-11 rounded-full transition ${
                                            isSelected ? 'bg-emerald-500' : 'bg-slate-600'
                                          }`}
                                        />
                                        <span
                                          aria-hidden="true"
                                          className={`absolute left-1 top-1 h-4 w-4 rounded-full bg-white transition-transform ${
                                            isSelected ? 'translate-x-5' : 'translate-x-0'
                                          }`}
                                        />
                                        {isFocusVisible && (
                                          <span className="absolute -inset-1 rounded-full ring-2 ring-emerald-300" aria-hidden="true" />
                                        )}
                                      </>
                                    )}
                                  </AriaSwitch>
                                </div>
                              ) : (
                                <input
                                  type="number"
                                  inputMode="decimal"
                                  min={minValue}
                                  step={setting.value_type === 'int' ? 1 : 0.1}
                                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/30"
                                  placeholder={String(placeholderValue)}
                                  value={draftValue}
                                  onChange={(event) => {
                                    const value = event.target.value
                                    setDrafts((prev) => ({
                                      ...prev,
                                      [setting.key]: value,
                                    }))
                                    setDirtyKeys((prev) => ({
                                      ...prev,
                                      [setting.key]: true,
                                    }))
                                    if (status?.error) {
                                      updateRowError(setting.key, null)
                                    }
                                  }}
                                />
                              )}
                              {setting.disable_value !== null && setting.disable_value !== undefined && (
                                <p className="text-xs text-slate-500">
                                  Use {setting.disable_value} to disable this limit.
                                </p>
                              )}
                              {hasOverride && (
                                <div className="flex flex-wrap gap-2">
                                  <button
                                    type="button"
                                    className={buttonStyles.reset}
                                    onClick={() => {
                                      setDrafts((prev) => ({
                                        ...prev,
                                        [setting.key]: '',
                                      }))
                                      setDirtyKeys((prev) => ({
                                        ...prev,
                                        [setting.key]: true,
                                      }))
                                      if (status?.error) {
                                        updateRowError(setting.key, null)
                                      }
                                    }}
                                    disabled={saving}
                                  >
                                    Reset to default
                                  </button>
                                </div>
                              )}
                              {status?.error && (
                                <p className="flex items-center gap-2 text-xs text-rose-600">
                                  <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                                  {status.error}
                                </p>
                              )}
                              {!status?.error && hasOverride && null}
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </section>
              ))
            )}
          </div>
        </div>
      </div>
      <SaveBar visible={hasChanges} onCancel={handleCancelAll} onSave={handleSaveAll} busy={saving} error={saveError} />
    </div>
  )
}

type SaveBarProps = {
  visible: boolean
  onCancel: () => void
  onSave: () => Promise<void> | void
  busy?: boolean
  error?: string | null
}

function SaveBar({ visible, onCancel, onSave, busy, error }: SaveBarProps) {
  if (!visible) {
    return null
  }
  return (
    <div id="system-settings-save-bar" className="fixed inset-x-0 bottom-0 z-40 pointer-events-none">
      <div className="pointer-events-auto mx-auto w-full max-w-5xl px-4 pb-4">
        <div className="flex flex-col gap-3 rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-[0_8px_30px_rgba(15,23,42,0.25)] sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-col gap-1 text-sm text-gray-700">
            <div className="flex items-center gap-2">
              <Info className="h-4 w-4 text-blue-600" aria-hidden="true" />
              <span>You have unsaved changes</span>
            </div>
            {error && (
              <div className="flex items-center gap-2 text-xs text-red-600">
                <XCircle className="h-4 w-4" aria-hidden="true" />
                <span>{error}</span>
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg border border-transparent bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-60"
            >
              <Check className="h-4 w-4" aria-hidden="true" />
              {busy ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
