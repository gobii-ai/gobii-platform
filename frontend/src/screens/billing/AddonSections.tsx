import type { ReactNode } from 'react'
import { BadgeCheck, GlobeLock, Layers3, Plus, Users } from 'lucide-react'

import type { BillingAddonKindKey, BillingInitialData } from './types'
import type { BillingDraftAction, BillingDraftState } from './draft'
import { buildAddonOptionLabel, buildInitialAddonQuantityMap, formatCents, normalizeCurrency } from './utils'
import { StagedRow } from './StagedRow'
import { ToggleSwitch } from './ToggleSwitch'

type AddonSectionsProps = {
  initialData: BillingInitialData
  draft: BillingDraftState
  dispatch: (action: BillingDraftAction) => void
  saving: boolean
  addonsInteractable: boolean
  addonsDisabledReason: string | null
}

type AddonSectionMeta = {
  key: BillingAddonKindKey
  title: string
  description: string
  icon: ReactNode
}

const ADDON_SECTIONS: AddonSectionMeta[] = [
  {
    key: 'taskPack',
    title: 'Task Packs',
    description: 'Add more monthly tasks to this subscription.',
    icon: <Layers3 className="h-5 w-5" />,
  },
  {
    key: 'contactPack',
    title: 'Contact Packs',
    description: 'Increase your contacts per agent limit.',
    icon: <Users className="h-5 w-5" />,
  },
  {
    key: 'browserTaskPack',
    title: 'Browser Task Packs',
    description: 'Increase browser task throughput limits.',
    icon: <GlobeLock className="h-5 w-5" />,
  },
  {
    key: 'advancedCaptcha',
    title: 'Advanced CAPTCHA',
    description: 'Enable advanced CAPTCHA resolution support.',
    icon: <BadgeCheck className="h-5 w-5" />,
  },
]

export function AddonSections({
  initialData,
  draft,
  dispatch,
  saving,
  addonsInteractable,
  addonsDisabledReason,
}: AddonSectionsProps) {
  const initialAddonQuantities = buildInitialAddonQuantityMap(initialData.addons)

  const captchaOptions = initialData.addons.kinds.advancedCaptcha?.options ?? []
  const captchaPriceIds = captchaOptions.map((opt) => opt.priceId).filter(Boolean)
  const captchaEnabled = captchaPriceIds.some((pid) => (draft.addonQuantities[pid] ?? 0) > 0)
  const captchaActivePriceId = (() => {
    const active = captchaOptions.find((opt) => (draft.addonQuantities[opt.priceId] ?? 0) > 0)
    if (active?.priceId) return active.priceId
    return captchaOptions[0]?.priceId ?? ''
  })()

  return (
    <section className="card" data-section="billing-addons">
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
          <Layers3 className="h-4 w-4 text-slate-500" />
          <span>Add-ons</span>
        </div>
        <p className="text-sm text-slate-600">Stage changes here, then save at the bottom.</p>
      </div>

      {addonsDisabledReason ? (
        <div className="mt-5 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
          {addonsDisabledReason}
        </div>
      ) : null}

      <div className="mt-6 grid grid-cols-1 gap-10 lg:grid-cols-2">
        {ADDON_SECTIONS.map((section) => {
          const options = initialData.addons.kinds[section.key]?.options ?? []
          const selectableOptions = options.filter((opt) => opt.priceId)
          const selected = draft.selectedAddonByKind[section.key] ?? ''

          if (section.key === 'advancedCaptcha') {
            const option = captchaOptions[0] ?? null
            const inferredCurrency = normalizeCurrency(option?.currency || initialData.addons.totals.currency)
            const unitCents = typeof option?.unitAmount === 'number' ? option.unitAmount : null
            const priceHint = unitCents !== null ? `${formatCents(unitCents, inferredCurrency)}/mo` : null

            return (
              <div key={section.key} className="rounded-2xl border border-slate-200 p-5">
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 grid h-11 w-11 place-items-center rounded-2xl bg-blue-50 text-blue-700">
                    {section.icon}
                  </div>
                  <div className="min-w-0">
                    <div className="text-base font-bold text-slate-900">{section.title}</div>
                    <div className="mt-1 text-sm text-slate-600">{section.description}</div>
                  </div>
                </div>

                <div className="mt-5 space-y-3">
                  {captchaOptions.length ? (
                    <ToggleSwitch
                      checked={captchaEnabled}
                      disabled={!addonsInteractable || saving}
                      label={captchaEnabled ? 'Enabled' : 'Disabled'}
                      description={priceHint ? `Billed monthly (${priceHint}).` : 'Billed monthly.'}
                      onChange={(enabled) =>
                        dispatch({
                          type: 'captcha.setEnabled',
                          enabled,
                          priceIds: captchaPriceIds,
                          activePriceId: captchaActivePriceId,
                        })
                      }
                    />
                  ) : (
                    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
                      No options are configured for this add-on.
                    </div>
                  )}
                </div>
              </div>
            )
          }

          const rows = selectableOptions
            .map((opt) => {
              const currentQty = initialAddonQuantities[opt.priceId] ?? 0
              const nextQty = draft.addonQuantities[opt.priceId] ?? 0
              const visible = currentQty > 0 || nextQty > 0
              return { opt, currentQty, nextQty, visible }
            })
            .filter((row) => row.visible)

          return (
            <div key={section.key} className="rounded-2xl border border-slate-200 p-5">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 grid h-11 w-11 place-items-center rounded-2xl bg-blue-50 text-blue-700">
                  {section.icon}
                </div>
                <div className="min-w-0">
                  <div className="text-base font-bold text-slate-900">{section.title}</div>
                  <div className="mt-1 text-sm text-slate-600">{section.description}</div>
                </div>
              </div>

              <div className="mt-5 space-y-4">
                {rows.length ? (
                  <div className="space-y-2">
                    {rows.map(({ opt, currentQty, nextQty }) => {
                      const isRemoved = currentQty > 0 && nextQty === 0
                      const isChanged = currentQty !== nextQty
                      const currency = normalizeCurrency(opt.currency || initialData.addons.totals.currency || 'USD')
                      const lineCents = (typeof opt.unitAmount === 'number' ? opt.unitAmount : 0) * nextQty

                      return (
                        <StagedRow
                          key={opt.priceId}
                          title={buildAddonOptionLabel(section.key, opt)}
                          badge={
                            isChanged ? (
                              <span className="text-amber-700">
                                {isRemoved ? 'Will be removed' : `Will change (${currentQty} → ${nextQty})`}
                              </span>
                            ) : null
                          }
                          subtitle={
                            <>
                              Quantity: <span className="font-semibold text-slate-900 tabular-nums">{nextQty}</span>
                              {typeof opt.unitAmount === 'number' && nextQty > 0 ? (
                                <>
                                  {' '}
                                  · {formatCents(lineCents, currency)}
                                </>
                              ) : null}
                            </>
                          }
                          actions={
                            <>
                              {nextQty > 0 ? (
                                <button
                                  type="button"
                                  onClick={() => dispatch({ type: 'addon.remove', priceId: opt.priceId })}
                                  disabled={!addonsInteractable || saving}
                                  className="inline-flex items-center justify-center rounded-xl border border-rose-200 bg-white px-3 py-2 text-sm font-semibold text-rose-700 transition hover:border-rose-300 disabled:opacity-50"
                                >
                                  Remove
                                </button>
                              ) : null}
                              {currentQty > 0 && nextQty === 0 ? (
                                <button
                                  type="button"
                                  onClick={() =>
                                    dispatch({
                                      type: 'addon.undo',
                                      priceId: opt.priceId,
                                      initialQty: initialAddonQuantities[opt.priceId] ?? 0,
                                    })
                                  }
                                  disabled={!addonsInteractable || saving}
                                  className="inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-300 disabled:opacity-50"
                                >
                                  Undo
                                </button>
                              ) : null}
                            </>
                          }
                        />
                      )
                    })}
                  </div>
                ) : (
                  <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
                    No {section.title.toLowerCase()} are currently active.
                  </div>
                )}

                {selectableOptions.length ? (
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <div className="relative flex-1">
                      <select
                        value={selected}
                        onChange={(event) =>
                          dispatch({
                            type: 'addon.selectOption',
                            kind: section.key,
                            priceId: event.target.value,
                          })
                        }
                        disabled={!addonsInteractable || saving}
                        className="w-full appearance-none rounded-xl border border-slate-200 bg-white px-3 py-2.5 pr-10 text-sm font-semibold text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500/30 disabled:opacity-50"
                        aria-label={`Select ${section.title} option`}
                      >
                        <option value="">Select an option…</option>
                        {selectableOptions.map((opt) => (
                          <option key={opt.priceId} value={opt.priceId}>
                            {buildAddonOptionLabel(section.key, opt)}
                            {opt.priceDisplay ? ` (${opt.priceDisplay}/mo)` : ''}
                          </option>
                        ))}
                      </select>
                    </div>
                    <button
                      type="button"
                      onClick={() => dispatch({ type: 'addon.addSelected', kind: section.key })}
                      disabled={!addonsInteractable || saving || !selected}
                      className="inline-flex items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:opacity-50"
                    >
                      <Plus className="h-4 w-4" />
                      Add
                    </button>
                  </div>
                ) : (
                  <div className="text-sm text-slate-600">No options are configured for this add-on.</div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
