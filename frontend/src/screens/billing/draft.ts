import type { BillingAddonKindKey, BillingInitialData, DedicatedIpProxy } from './types'
import { buildInitialAddonQuantityMap } from './utils'

export type BillingDraftState = {
  seatTarget: number | null
  cancelSeatSchedule: boolean
  addonQuantities: Record<string, number>
  selectedAddonByKind: Record<BillingAddonKindKey, string>
  dedicatedAddQty: number
  dedicatedRemoveIds: string[]
  dedicatedUnassignIds: string[]
}

export type BillingDraftAction =
  | { type: 'reset'; initialData: BillingInitialData }
  | { type: 'seat.setTarget'; value: number }
  | { type: 'seat.adjust'; delta: number; min: number }
  | { type: 'seat.cancelSchedule' }
  | { type: 'addon.selectOption'; kind: BillingAddonKindKey; priceId: string }
  | { type: 'addon.addSelected'; kind: BillingAddonKindKey }
  | { type: 'addon.remove'; priceId: string }
  | { type: 'addon.undo'; priceId: string; initialQty: number }
  | { type: 'captcha.setEnabled'; enabled: boolean; priceIds: string[]; activePriceId: string }
  | { type: 'dedicated.setAddQty'; value: number }
  | { type: 'dedicated.stageRemove'; proxy: DedicatedIpProxy; unassign: boolean }
  | { type: 'dedicated.undoRemove'; proxyId: string }

function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return min
  }
  return Math.max(min, Math.min(max, Math.trunc(value)))
}

export function initialDraftState(initialData: BillingInitialData): BillingDraftState {
  return {
    seatTarget: initialData.contextType === 'organization' ? initialData.seats.purchased : null,
    cancelSeatSchedule: false,
    addonQuantities: buildInitialAddonQuantityMap(initialData.addons),
    selectedAddonByKind: { taskPack: '', contactPack: '', browserTaskPack: '', advancedCaptcha: '' },
    dedicatedAddQty: 0,
    dedicatedRemoveIds: [],
    dedicatedUnassignIds: [],
  }
}

export function billingDraftReducer(state: BillingDraftState, action: BillingDraftAction): BillingDraftState {
  switch (action.type) {
    case 'reset':
      return initialDraftState(action.initialData)
    case 'seat.setTarget':
      return { ...state, seatTarget: clampInt(action.value, 0, 9999), cancelSeatSchedule: false }
    case 'seat.adjust': {
      const current = state.seatTarget ?? 0
      const next = Math.max(action.min, current + action.delta)
      return { ...state, seatTarget: next, cancelSeatSchedule: false }
    }
    case 'seat.cancelSchedule':
      return { ...state, cancelSeatSchedule: true }
    case 'addon.selectOption':
      return {
        ...state,
        selectedAddonByKind: { ...state.selectedAddonByKind, [action.kind]: action.priceId },
      }
    case 'addon.addSelected': {
      const selected = (state.selectedAddonByKind[action.kind] || '').trim()
      if (!selected) {
        return state
      }
      const current = state.addonQuantities[selected] ?? 0
      return {
        ...state,
        addonQuantities: { ...state.addonQuantities, [selected]: Math.min(999, current + 1) },
      }
    }
    case 'addon.remove':
      return {
        ...state,
        addonQuantities: { ...state.addonQuantities, [action.priceId]: 0 },
      }
    case 'addon.undo':
      return {
        ...state,
        addonQuantities: { ...state.addonQuantities, [action.priceId]: clampInt(action.initialQty, 0, 999) },
      }
    case 'captcha.setEnabled': {
      const nextQuantities = { ...state.addonQuantities }
      action.priceIds.forEach((pid) => {
        nextQuantities[pid] = 0
      })
      if (action.enabled && action.activePriceId) {
        nextQuantities[action.activePriceId] = 1
      }
      return { ...state, addonQuantities: nextQuantities }
    }
    case 'dedicated.setAddQty':
      return { ...state, dedicatedAddQty: clampInt(action.value, 0, 99) }
    case 'dedicated.stageRemove': {
      const proxyId = action.proxy.id
      if (state.dedicatedRemoveIds.includes(proxyId)) {
        return state
      }
      const nextRemove = [...state.dedicatedRemoveIds, proxyId]
      const nextUnassign = action.unassign && !state.dedicatedUnassignIds.includes(proxyId)
        ? [...state.dedicatedUnassignIds, proxyId]
        : state.dedicatedUnassignIds
      return { ...state, dedicatedRemoveIds: nextRemove, dedicatedUnassignIds: nextUnassign }
    }
    case 'dedicated.undoRemove': {
      const proxyId = action.proxyId
      return {
        ...state,
        dedicatedRemoveIds: state.dedicatedRemoveIds.filter((id) => id !== proxyId),
        dedicatedUnassignIds: state.dedicatedUnassignIds.filter((id) => id !== proxyId),
      }
    }
    default:
      return state
  }
}

