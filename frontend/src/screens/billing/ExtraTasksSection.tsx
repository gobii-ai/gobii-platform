import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Infinity, Zap } from 'lucide-react'

import { jsonRequest } from '../../api/http'
import type { BillingExtraTasksSettings, BillingInitialData } from './types'
import { ToggleSwitch } from './ToggleSwitch'

type ExtraTasksSectionProps = {
  initialData: BillingInitialData
}

export function ExtraTasksSection({ initialData }: ExtraTasksSectionProps) {
  const initial = initialData.extraTasks
  const isTrialing = Boolean(initialData.trial?.isTrialing)
  const eligible = useMemo(() => {
    if (isTrialing) return false
    if (initialData.contextType === 'personal') {
      return Boolean(initialData.paidSubscriber)
    }
    return Boolean(initialData.seats.hasStripeSubscription && initialData.seats.purchased > 0)
  }, [initialData, isTrialing])

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const [settings, setSettings] = useState<BillingExtraTasksSettings>(initial)
  const [maxTasksDraft, setMaxTasksDraft] = useState(() => String(initial.maxTasks))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setSettings(initial)
    setMaxTasksDraft(String(initial.maxTasks))
    setError(null)
  }, [initial])

  const canModify = Boolean(settings.canModify) && !busy

  const postUpdate = useCallback(async (payload: { enabled: boolean; infinite: boolean; maxTasks: number }) => {
    setBusy(true)
    setError(null)
    try {
      const result = await jsonRequest<{ success: boolean; extra_tasks?: BillingExtraTasksSettings; max_extra_tasks?: number; error?: string }>(
        settings.endpoints.updateUrl,
        {
          method: 'POST',
          includeCsrf: true,
          json: payload,
        },
      )
      if (!result?.success) {
        // Log server-provided error for debugging, but keep UI copy generic.
        // eslint-disable-next-line no-console
        console.error('Failed to update extra tasks settings:', result?.error)
        throw new Error(result?.error || 'Update failed')
      }
      const next = result.extra_tasks
      if (!next) {
        throw new Error('Update succeeded but response was missing extra_tasks')
      }
      if (mountedRef.current) {
        setSettings(next)
        setMaxTasksDraft(String(next.maxTasks))
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('Extra tasks update error:', err)
      if (mountedRef.current) {
        setError('An error occurred while updating billing. Please contact support@gobii.ai for help.')
      }
    } finally {
      if (mountedRef.current) {
        setBusy(false)
      }
    }
  }, [settings.canModify, settings.endpoints])

  const handleEnabledChange = useCallback(async (nextEnabled: boolean) => {
    const next = {
      enabled: nextEnabled,
      infinite: nextEnabled ? settings.infinite : false,
      maxTasks: nextEnabled ? settings.maxTasks : 5,
    }
    await postUpdate(next)
  }, [postUpdate, settings.infinite, settings.maxTasks])

  const handleInfiniteSelect = useCallback(async (nextInfinite: boolean) => {
    const parsed = Number.parseInt(maxTasksDraft, 10)
    const maxTasks = Number.isFinite(parsed) ? Math.max(1, parsed) : settings.maxTasks
    await postUpdate({
      enabled: true,
      infinite: nextInfinite,
      maxTasks,
    })
  }, [maxTasksDraft, postUpdate, settings.maxTasks])

  const handleMaxTasksCommit = useCallback(async () => {
    const parsed = Number.parseInt(maxTasksDraft, 10)
    if (!Number.isFinite(parsed) || parsed < 1) {
      setMaxTasksDraft(String(settings.maxTasks))
      return
    }
    await postUpdate({
      enabled: true,
      infinite: false,
      maxTasks: parsed,
    })
  }, [maxTasksDraft, postUpdate, settings.maxTasks])

  if (!eligible) {
    return null
  }

  return (
    <section className="card" data-section="billing-extra-tasks">
      <div className="flex min-w-0 items-start gap-3">
        <div className="mt-0.5 grid h-9 w-9 place-items-center rounded-2xl bg-blue-50 text-blue-700">
          <Zap className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <div className="text-base font-bold text-slate-900">Additional tasks</div>
          <div className="mt-1 text-sm text-slate-600">
            Auto-purchase task overage when you run out of included credits.
          </div>
        </div>
      </div>

      <div className="mt-4 space-y-3">
        <ToggleSwitch
          checked={settings.enabled}
          disabled={!canModify}
          label={settings.enabled ? 'Auto-purchase enabled' : 'Auto-purchase disabled'}
          description="Billed as metered usage. Changes apply immediately."
          onChange={(nextEnabled) => {
            void handleEnabledChange(nextEnabled)
          }}
        />

        {settings.enabled ? (
          <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
            <div className="space-y-2">
              <label className="flex cursor-pointer items-center gap-3 rounded-xl px-2 py-2 hover:bg-blue-50/30">
                <input
                  type="radio"
                  checked={!settings.infinite}
                  onChange={() => (canModify ? void handleInfiniteSelect(false) : undefined)}
                  disabled={!canModify}
                  className="h-4 w-4"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="text-sm font-semibold text-slate-900">Up to</div>
                    <input
                      type="number"
                      min={1}
                      max={100000}
                      value={maxTasksDraft}
                      disabled={!canModify || settings.infinite}
                      onChange={(event) => setMaxTasksDraft(event.target.value)}
                      onBlur={() => {
                        if (!canModify || settings.infinite) return
                        void handleMaxTasksCommit()
                      }}
                      onKeyDown={(event) => {
                        if (event.key !== 'Enter') return
                        if (!canModify || settings.infinite) return
                        event.currentTarget.blur()
                      }}
                      className="h-9 w-24 rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-500/30 disabled:opacity-60"
                    />
                    <div className="text-sm font-semibold text-slate-900">additional tasks</div>
                    <div className="text-xs font-medium text-slate-500">per period</div>
                  </div>
                </div>
              </label>

              <label className="flex cursor-pointer items-center gap-3 rounded-xl px-2 py-2 hover:bg-blue-50/30">
                <input
                  type="radio"
                  checked={settings.infinite}
                  onChange={() => (canModify ? void handleInfiniteSelect(true) : undefined)}
                  disabled={!canModify}
                  className="h-4 w-4"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                    <Infinity className="h-4 w-4 text-blue-700" />
                    Unlimited additional tasks
                  </div>
                  <div className="mt-1 text-xs text-slate-600">No cap. Charges apply based on metered usage.</div>
                </div>
              </label>
            </div>
          </div>
        ) : null}

        {error ? (
          <div className="rounded-2xl border border-rose-200 bg-white px-4 py-3 text-sm font-semibold text-rose-700">
            {error}
          </div>
        ) : null}
      </div>
    </section>
  )
}
