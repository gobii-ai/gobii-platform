import type { ReactNode } from 'react'
import { BadgeCheck, GlobeLock, Layers3, Minus, Plus, Users } from 'lucide-react'

import type { BillingAddonKindKey, BillingInitialData, DedicatedIpProxy } from './types'
import type { BillingDraftAction, BillingDraftState } from './draft'
import { buildAddonOptionLabel, formatCents, normalizeCurrency } from './utils'
import { ToggleSwitch } from './ToggleSwitch'
import { DedicatedIpSection } from './DedicatedIpSection'

type AddonSectionsProps = {
  initialData: BillingInitialData
  draft: BillingDraftState
  dispatch: (action: BillingDraftAction) => void
  saving: boolean
  addonsInteractable: boolean
  addonsDisabledReason: string | null
  dedicatedInteractable: boolean
  onRequestDedicatedRemove: (proxy: DedicatedIpProxy) => void
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
  dedicatedInteractable,
  onRequestDedicatedRemove,
}: AddonSectionsProps) {
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
        <p className="text-sm text-slate-600">Make changes here, then save at the bottom.</p>
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
                {selectableOptions.length ? (
                  <div className="space-y-2">
                    {selectableOptions.map((opt) => {
                      const qty = draft.addonQuantities[opt.priceId] ?? 0
                      const label = buildAddonOptionLabel(section.key, opt)
                      const currency = normalizeCurrency(opt.currency || initialData.addons.totals.currency || 'USD')
                      const unitCents = typeof opt.unitAmount === 'number' ? opt.unitAmount : null
                      const priceLabel = unitCents !== null
                        ? `${formatCents(unitCents, currency)}/mo`
                        : (opt.priceDisplay ? `${opt.priceDisplay}/mo` : null)
                      return (
                        <div
                          key={opt.priceId}
                          className={[
                            'flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 transition sm:flex-row sm:items-center sm:justify-between',
                            'hover:border-blue-200 hover:bg-blue-50/30',
                            (!addonsInteractable || saving) ? 'opacity-50' : '',
                          ].join(' ')}
                        >
                          <div className="min-w-0">
                            <div className="text-sm font-bold text-slate-900 leading-snug break-words">
                              {label}
                            </div>
                            {priceLabel ? (
                              <div className="mt-1 text-xs font-semibold text-slate-500">{priceLabel}</div>
                            ) : null}
                          </div>
                          <div className="flex flex-none items-center justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => dispatch({ type: 'addon.adjust', priceId: opt.priceId, delta: -1 })}
                              disabled={!addonsInteractable || saving || qty <= 0}
                              className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-60"
                              aria-label={`Decrease ${label}`}
                            >
                              <Minus className="h-4 w-4" strokeWidth={3} />
                            </button>
                            <div className="min-w-[3.25rem] rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-center text-sm font-bold text-slate-900 tabular-nums">
                              {qty}
                            </div>
                            <button
                              type="button"
                              onClick={() => dispatch({ type: 'addon.adjust', priceId: opt.priceId, delta: 1 })}
                              disabled={!addonsInteractable || saving || qty >= 999}
                              className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-blue-600 text-white shadow-sm transition hover:bg-blue-700 disabled:opacity-60"
                              aria-label={`Increase ${label}`}
                            >
                              <Plus className="h-4 w-4" strokeWidth={3} />
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                ) : (
                  <div className="text-sm text-slate-600">No options are configured for this add-on.</div>
                )}
              </div>
            </div>
          )
        })}

        <DedicatedIpSection
          initialData={initialData}
          draft={draft}
          dispatch={dispatch}
          saving={saving}
          dedicatedInteractable={dedicatedInteractable}
          onRequestRemove={onRequestDedicatedRemove}
        />
      </div>
    </section>
  )
}
