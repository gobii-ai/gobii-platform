import { GlobeLock, Minus, Plus } from 'lucide-react'

import type { BillingInitialData, DedicatedIpProxy } from './types'
import type { BillingDraftAction, BillingDraftState } from './draft'
import { formatCents, normalizeCurrency } from './utils'
import { StagedRow } from './StagedRow'

type DedicatedIpSectionProps = {
  initialData: BillingInitialData
  draft: BillingDraftState
  dispatch: (action: BillingDraftAction) => void
  saving: boolean
  dedicatedInteractable: boolean
  onRequestRemove: (proxy: DedicatedIpProxy) => void
}

export function DedicatedIpSection({
  initialData,
  draft,
  dispatch,
  saving,
  dedicatedInteractable,
  onRequestRemove,
}: DedicatedIpSectionProps) {
  const planCurrency = normalizeCurrency((initialData.plan?.currency as string | undefined) ?? 'USD')
  const current = initialData.dedicatedIps.proxies.length
  const removed = draft.dedicatedRemoveIds.length
  const effective = Math.max(0, current + draft.dedicatedAddQty - removed)
  const unitCents = Math.max(0, Math.round((initialData.dedicatedIps.unitPrice || 0) * 100))
  const inferredCurrency = normalizeCurrency(initialData.dedicatedIps.currency || planCurrency)

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-6">
      <div className="flex items-start gap-3 sm:w-72">
        <div className="mt-0.5 grid h-11 w-11 place-items-center rounded-2xl bg-indigo-50 text-indigo-700">
          <GlobeLock className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="text-base font-bold text-slate-900">Dedicated IPs</div>
          <div className="mt-1 text-sm text-slate-600">Reserved static IP addresses for this subscription.</div>
        </div>
      </div>

      <div className="min-w-0 flex-1 space-y-4">
        <div className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-semibold text-slate-900">{effective} reserved</div>
            {unitCents ? (
              <div className="text-xs font-semibold text-slate-500">{formatCents(unitCents, inferredCurrency)}/IP/mo</div>
            ) : null}
          </div>
          {!initialData.dedicatedIps.multiAssign ? (
            <div className="text-xs text-amber-700">Each dedicated IP can be assigned to only one agent at a time.</div>
          ) : null}
        </div>

        <div className="space-y-2">
          {initialData.dedicatedIps.proxies.length ? (
            initialData.dedicatedIps.proxies.map((proxy) => {
              const stagedRemove = draft.dedicatedRemoveIds.includes(proxy.id)
              const label = proxy.label || proxy.staticIp || proxy.host
              return (
                <StagedRow
                  key={proxy.id}
                  title={label}
                  badge={stagedRemove ? <span className="text-amber-700">Will be removed</span> : null}
                  subtitle={
                    proxy.assignedAgents.length
                      ? `In use by ${proxy.assignedAgents.map((a) => a.name).join(', ')}`
                      : 'Not assigned to any agents.'
                  }
                  actions={
                    stagedRemove ? (
                      <button
                        type="button"
                        onClick={() => dispatch({ type: 'dedicated.undoRemove', proxyId: proxy.id })}
                        disabled={saving}
                        className="inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-300 disabled:opacity-50"
                      >
                        Undo
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => onRequestRemove(proxy)}
                        disabled={!dedicatedInteractable || saving}
                        className="inline-flex items-center justify-center rounded-xl border border-rose-200 bg-white px-3 py-2 text-sm font-semibold text-rose-700 transition hover:border-rose-300 disabled:opacity-50"
                      >
                        Remove
                      </button>
                    )
                  }
                />
              )
            })
          ) : (
            <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
              No dedicated IPs are currently provisioned.
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="flex flex-1 items-center gap-2">
            <button
              type="button"
              onClick={() => dispatch({ type: 'dedicated.setAddQty', value: Math.max(0, draft.dedicatedAddQty - 1) })}
              disabled={!dedicatedInteractable || saving || draft.dedicatedAddQty <= 0}
              className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 disabled:opacity-50"
              aria-label="Decrease dedicated IP quantity to add"
            >
              <Minus className="h-4 w-4" strokeWidth={3} />
            </button>
            <div className="min-w-[5.5rem] rounded-xl border border-slate-200 bg-white px-4 py-2 text-center text-lg font-bold text-slate-900 tabular-nums">
              {draft.dedicatedAddQty}
            </div>
            <button
              type="button"
              onClick={() => dispatch({ type: 'dedicated.setAddQty', value: Math.min(99, draft.dedicatedAddQty + 1) })}
              disabled={!dedicatedInteractable || saving}
              className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 disabled:opacity-50"
              aria-label="Increase dedicated IP quantity to add"
            >
              <Plus className="h-4 w-4" strokeWidth={3} />
            </button>
          </div>

          <div className="text-sm text-slate-600">Add this many new dedicated IPs (staged).</div>
        </div>

        {!initialData.dedicatedIps.allowed ? (
          <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            Dedicated IPs require a paid plan.
          </div>
        ) : null}
      </div>
    </div>
  )
}

