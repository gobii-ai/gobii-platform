import { Atom, Globe, PlugZap, Shield } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'

import { useModal } from '../../hooks/useModal'
import { useLlmConfigFeedback, type ConfirmDialogConfig, type MutationOptions } from './shared'
import { ConfirmModalWrapper } from './modals'
import { useLlmConfigData } from './useLlmConfigData'
import { useLlmPerformanceRunner } from './useLlmPerformanceRunner'
import { useProviderEndpointActions } from './useProviderEndpointActions'
import { useRoutingTierActions } from './useRoutingTierActions'

export function useLlmConfigController() {
  const queryClient = useQueryClient()
    const { runWithFeedback, isBusy, activeLabels, notices, dismissNotice } = useLlmConfigFeedback()
    const [modal, showModal, closeModal] = useModal()
  
    const {
      selectedProfileId,
      setSelectedProfileId,
      profilesQuery,
      profileDetailQuery,
      selectedProfile,
      profiles,
      overviewQuery,
      intelligenceTiers,
      stats,
      providers,
      persistentStructures,
      embeddingTiers,
      fileHandlerTiers,
      imageGenerationSections,
      videoGenerationSections,
      browserTierGroups,
      endpointChoices,
    } = useLlmConfigData()
  
    const { performanceResult, handleRunPerformanceTest } = useLlmPerformanceRunner({
      endpointChoices,
      runWithFeedback,
    })
  
    const invalidateOverview = () => queryClient.invalidateQueries({ queryKey: ['llm-overview'] })
    const invalidateProfiles = () => queryClient.invalidateQueries({ queryKey: ['llm-routing-profiles'] })
    const invalidateProfileDetail = () => queryClient.invalidateQueries({ queryKey: ['llm-routing-profile', selectedProfileId] })
  
    const runMutation = async <T,>(action: () => Promise<T>, options?: MutationOptions) => {
      const { rethrow, ...feedbackOptions } = options ?? {}
      try {
        await runWithFeedback(async () => {
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
  
    const { endpointTestStatuses, providerHandlers } = useProviderEndpointActions({
      providers,
      runMutation,
      runWithFeedback,
      confirmDestructiveAction,
      invalidateProfileDetail,
    })
  
    const {
      pendingWeights,
      savingTierIds,
      dirtyTierIds,
      handleRangeUpdate,
      handleAddRange,
      handleRangeRemove,
      handleTierAdd,
      handleTierMove,
      handleTierRemove,
      stageTierEndpointWeight,
      commitTierEndpointWeights,
      handleTierEndpointRemove,
      handleTierEndpointReasoning,
      handleTierEndpointExtraction,
      handleBrowserTierAdd,
      handleBrowserTierMove,
      handleBrowserTierRemove,
      handleEmbeddingTierAdd,
      handleEmbeddingTierMove,
      handleEmbeddingTierRemove,
      handleFileHandlerTierAdd,
      handleFileHandlerTierMove,
      handleFileHandlerTierRemove,
      handleImageGenerationTierAdd,
      handleImageGenerationTierMove,
      handleImageGenerationTierRemove,
      handleVideoGenerationTierAdd,
      handleVideoGenerationTierMove,
      handleVideoGenerationTierRemove,
      handleTierEndpointAdd,
      openCreateProfileModal,
      handleCloneProfile,
      handleActivateProfile,
      handleDeleteProfile,
      openEditProfileModal,
      handleUpdateEvalJudge,
      handleUpdateSummarizationEndpoint,
      handleUpdateAgentJudgeEndpoint,
      handleProfileRangeAdd,
      handleProfileRangeUpdate,
      handleProfileRangeRemove,
      handleProfileTierAdd,
      handleProfileTierMove,
      handleProfileTierRemove,
      handleProfileBrowserTierAdd,
      handleProfileBrowserTierMove,
      handleProfileBrowserTierRemove,
      handleProfileEmbeddingTierAdd,
      handleProfileEmbeddingTierMove,
      handleProfileEmbeddingTierRemove,
      commitProfileTierEndpointWeights,
      handleProfileTierEndpointRemove,
      handleProfileTierEndpointExtraction,
    } = useRoutingTierActions({
      selectedProfile,
      selectedProfileId,
      setSelectedProfileId,
      profiles,
      persistentStructures,
      endpointChoices,
      overviewData: overviewQuery.data,
      runMutation,
      runWithFeedback,
      confirmDestructiveAction,
      showModal,
      isBusy,
      invalidateProfiles,
      invalidateProfileDetail,
    })
  
    const statsCards = [
      { label: 'Active providers', value: stats ? String(stats.active_providers) : '—', hint: 'Enabled vendors', icon: <PlugZap className="size-5" /> },
      { label: 'Persistent endpoints', value: stats ? String(stats.persistent_endpoints) : '—', hint: 'LLMs available for agents', icon: <Atom className="size-5" /> },
      { label: 'Browser models', value: stats ? String(stats.browser_endpoints) : '—', hint: 'Available to browser-use', icon: <Globe className="size-5" /> },
      { label: 'Premium tiers', value: stats ? String(stats.premium_persistent_tiers) : '—', hint: 'High-trust failover', icon: <Shield className="size-5" /> },
    ]

  return {
    modal,
    notices,
    activeLabels,
    dismissNotice,
    overviewQuery,
    statsCards,
    profilesQuery,
    profileDetailQuery,
    selectedProfile,
    profiles,
    selectedProfileId,
    setSelectedProfileId,
    isBusy,
    openCreateProfileModal,
    openEditProfileModal,
    handleCloneProfile,
    handleActivateProfile,
    handleDeleteProfile,
    endpointChoices,
    handleUpdateEvalJudge,
    handleUpdateSummarizationEndpoint,
    handleUpdateAgentJudgeEndpoint,
    providers,
    providerHandlers,
    endpointTestStatuses,
    showModal,
    closeModal,
    persistentStructures,
    intelligenceTiers,
    handleProfileRangeAdd,
    handleAddRange,
    handleProfileRangeUpdate,
    handleRangeUpdate,
    handleProfileRangeRemove,
    handleRangeRemove,
    handleProfileTierAdd,
    handleTierAdd,
    handleProfileTierMove,
    handleTierMove,
    handleProfileTierRemove,
    handleTierRemove,
    handleTierEndpointAdd,
    pendingWeights,
    savingTierIds,
    dirtyTierIds,
    stageTierEndpointWeight,
    commitProfileTierEndpointWeights,
    commitTierEndpointWeights,
    handleProfileTierEndpointRemove,
    handleTierEndpointRemove,
    handleTierEndpointReasoning,
    browserTierGroups,
    handleProfileBrowserTierAdd,
    handleBrowserTierAdd,
    handleProfileBrowserTierMove,
    handleBrowserTierMove,
    handleProfileBrowserTierRemove,
    handleBrowserTierRemove,
    handleProfileTierEndpointExtraction,
    handleTierEndpointExtraction,
    embeddingTiers,
    handleProfileEmbeddingTierAdd,
    handleEmbeddingTierAdd,
    handleProfileEmbeddingTierMove,
    handleEmbeddingTierMove,
    handleProfileEmbeddingTierRemove,
    handleEmbeddingTierRemove,
    fileHandlerTiers,
    handleFileHandlerTierAdd,
    handleFileHandlerTierMove,
    handleFileHandlerTierRemove,
    imageGenerationSections,
    handleImageGenerationTierAdd,
    handleImageGenerationTierMove,
    handleImageGenerationTierRemove,
    videoGenerationSections,
    handleVideoGenerationTierAdd,
    handleVideoGenerationTierMove,
    handleVideoGenerationTierRemove,
    performanceResult,
    handleRunPerformanceTest,
  }
}

export type LlmConfigController = ReturnType<typeof useLlmConfigController>
