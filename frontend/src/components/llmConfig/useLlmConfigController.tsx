import { Atom, Globe, PlugZap, Shield } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'

import { useModal } from '../../hooks/useModal'
import { useLlmConfigFeedback, type ConfirmDialogConfig, type MutationOptions } from './shared'
import { ConfirmModalWrapper } from './modals'
import { useLlmConfigData } from './useLlmConfigData'
import { useProviderEndpointActions } from './useProviderEndpointActions'
import { useRoutingTierActions } from './useRoutingTierActions'

export function useLlmConfigController() {
  const queryClient = useQueryClient()
  const feedback = useLlmConfigFeedback()
  const [modal, showModal, closeModal] = useModal()
  const data = useLlmConfigData()

  const invalidateOverview = () => queryClient.invalidateQueries({ queryKey: ['llm-overview'] })
  const invalidateProfiles = () => queryClient.invalidateQueries({ queryKey: ['llm-routing-profiles'] })
  const invalidateProfileDetail = () => queryClient.invalidateQueries({ queryKey: ['llm-routing-profile', data.selectedProfileId] })

  const runMutation = async <T,>(action: () => Promise<T>, options?: MutationOptions) => {
    const { rethrow, ...feedbackOptions } = options ?? {}
    try {
      await feedback.runWithFeedback(async () => {
        const result = await action()
        await invalidateOverview()
        return result
      }, feedbackOptions)
    } catch (error) {
      if (rethrow) throw error
    }
  }

  const requestConfirmation = (options: ConfirmDialogConfig) =>
    new Promise<void>((resolve, reject) => {
      showModal((onClose) =>
        <ConfirmModalWrapper
          options={options}
          onResolve={resolve}
          onReject={reject}
          onClose={onClose}
        />,
      )
    })

  const confirmDestructiveAction = (options: ConfirmDialogConfig) =>
    requestConfirmation({
      ...options,
      confirmLabel: options.confirmLabel ?? 'Delete',
      cancelLabel: options.cancelLabel ?? 'Cancel',
      intent: options.intent ?? 'danger',
    })

  const provider = useProviderEndpointActions({
    providers: data.providers,
    runMutation,
    runWithFeedback: feedback.runWithFeedback,
    confirmDestructiveAction,
    invalidateProfileDetail,
  })

  const routing = useRoutingTierActions({
    selectedProfile: data.selectedProfile,
    selectedProfileId: data.selectedProfileId,
    setSelectedProfileId: data.setSelectedProfileId,
    profiles: data.profiles,
    persistentStructures: data.persistentStructures,
    endpointChoices: data.endpointChoices,
    overviewData: data.overviewQuery.data,
    runMutation,
    runWithFeedback: feedback.runWithFeedback,
    confirmDestructiveAction,
    showModal,
    isBusy: feedback.isBusy,
    invalidateProfiles,
    invalidateProfileDetail,
  })

  const stats = data.stats
  const statsCards = [
    { label: 'Active providers', value: stats ? String(stats.active_providers) : '—', hint: 'Enabled vendors', icon: <PlugZap className="size-5" /> },
    { label: 'Persistent endpoints', value: stats ? String(stats.persistent_endpoints) : '—', hint: 'LLMs available for agents', icon: <Atom className="size-5" /> },
    { label: 'Browser models', value: stats ? String(stats.browser_endpoints) : '—', hint: 'Available to browser-use', icon: <Globe className="size-5" /> },
    { label: 'Premium tiers', value: stats ? String(stats.premium_persistent_tiers) : '—', hint: 'High-trust failover', icon: <Shield className="size-5" /> },
  ]

  return { data, feedback, modal, showModal, closeModal, statsCards, provider, routing }
}

export type LlmConfigController = ReturnType<typeof useLlmConfigController>
