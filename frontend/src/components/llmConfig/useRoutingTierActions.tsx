import { useEffect, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import { createPortal } from 'react-dom'

import * as llmApi from '../../api/llmConfig'
import { AddEndpointModal, CreateProfileModal, EditProfileModal } from './modals'
import { actionKey, addProfileTierEndpointByScope, addTierEndpointByScope, deleteProfileTierEndpointByScope, deleteTierEndpointByScope, distributeEvenWeights, encodeServerWeight, ensureServerUnits, IMAGE_GENERATION_SECTION_CONFIG, MIN_SERVER_UNIT, rebalanceTierWeights, updateProfileTierEndpointByScope, updateTierEndpointByScope, VIDEO_GENERATION_SECTION_CONFIG, type AsyncFeedback, type ConfirmDialogConfig, type ImageGenerationUseCase, type MutationOptions, type Tier, type TierEndpoint, type TierScope, type TokenRange, type VideoGenerationUseCase } from './shared'

type RunMutation = <T>(action: () => Promise<T>, options?: MutationOptions) => Promise<void>
type ShowModal = (renderer: (onClose: () => void) => ReactNode) => void

type UseRoutingTierActionsArgs = {
  selectedProfile: llmApi.RoutingProfileDetail | null
  selectedProfileId: string | null
  setSelectedProfileId: Dispatch<SetStateAction<string | null>>
  profiles: llmApi.RoutingProfileListItem[]
  persistentStructures: { ranges: TokenRange[]; tiers: Tier[] }
  endpointChoices: llmApi.EndpointChoices
  overviewData?: llmApi.LlmOverviewResponse
  runMutation: RunMutation
  runWithFeedback: AsyncFeedback['runWithFeedback']
  confirmDestructiveAction: (options: ConfirmDialogConfig) => Promise<void>
  showModal: ShowModal
  isBusy: AsyncFeedback['isBusy']
  invalidateProfiles: () => Promise<unknown>
  invalidateProfileDetail: () => Promise<unknown>
}

export function useRoutingTierActions({
  selectedProfile,
  selectedProfileId,
  setSelectedProfileId,
  profiles,
  persistentStructures,
  endpointChoices,
  overviewData,
  runMutation,
  runWithFeedback,
  confirmDestructiveAction,
  showModal,
  isBusy,
  invalidateProfiles,
  invalidateProfileDetail,
}: UseRoutingTierActionsArgs) {
  const [pendingWeights, setPendingWeights] = useState<Record<string, number>>({})
  const [savingTierIds, setSavingTierIds] = useState<Set<string>>(new Set())
  const [dirtyTierIds, setDirtyTierIds] = useState<Set<string>>(new Set())
  const stagedWeightsRef = useRef<Record<string, { scope: TierScope; updates: { id: string; weight: number }[] }>>({})

  useEffect(() => {
    setPendingWeights({})
    setDirtyTierIds(new Set())
  }, [overviewData, selectedProfile])



  const handleRangeUpdate = (rangeId: string, field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => {
    const payload: Record<string, string | number | null> = {}
    payload[field] = value
    return runMutation(() => llmApi.updateTokenRange(rangeId, payload), {
      label: 'Saving range…',
      busyKey: actionKey('range', rangeId, field),
      context: 'Token range',
      rethrow: true,
    })
  }
  
  const handleAddRange = () => {
    const sorted = [...persistentStructures.ranges].sort((a, b) => (a.max_tokens ?? Infinity) - (b.max_tokens ?? Infinity))
    const last = sorted.at(-1)
    const baseMin = last?.max_tokens ?? 0
    const name = `Range ${sorted.length + 1}`
    return runMutation(
      () => llmApi.createTokenRange({ name, min_tokens: baseMin, max_tokens: baseMin + 10000 }),
      {
        successMessage: 'Range added',
        label: 'Creating range…',
        busyKey: actionKey('range', 'create'),
        context: name,
      },
    )
  }
  
  const handleRangeRemove = (range: TokenRange) =>
    confirmDestructiveAction({
      title: `Delete range "${range.name}"?`,
      message: 'All tiers in this token range will also be deleted. Provider endpoints remain available for reuse.',
      confirmLabel: 'Delete range',
      onConfirm: () =>
        runMutation(() => llmApi.deleteTokenRange(range.id), {
          successMessage: 'Range removed',
          label: 'Removing range…',
          busyKey: actionKey('range', range.id, 'remove'),
          context: range.name,
        }),
    })
  
  const handleTierAdd = (rangeId: string, intelligenceTierKey: string) => {
    return runMutation(() => llmApi.createPersistentTier(rangeId, { intelligence_tier: intelligenceTierKey }), {
      successMessage: 'Tier added',
      label: 'Creating tier…',
      busyKey: actionKey('range', rangeId, `add-${intelligenceTierKey}-tier`),
      context: 'Persistent tier',
    })
  }
  const handleTierMove = (rangeId: string, tierId: string, direction: 'up' | 'down') =>
    runMutation(() => llmApi.updatePersistentTier(tierId, { move: direction }), {
      label: direction === 'up' ? 'Moving tier up…' : 'Moving tier down…',
      busyKey: actionKey('persistent', tierId, 'move', direction),
      busyKeys: [actionKey('persistent', tierId, 'move'), actionKey('persistent-range', rangeId, 'move')],
      context: 'Persistent tier',
    })
  const handleTierRemove = (tier: Tier) =>
    confirmDestructiveAction({
      title: `Delete tier "${tier.name}"?`,
      message: 'Endpoints will be detached from this persistent tier.',
      confirmLabel: 'Delete tier',
      onConfirm: () => runMutation(() => llmApi.deletePersistentTier(tier.id), {
        successMessage: 'Tier removed',
        label: 'Removing tier…',
        busyKey: actionKey('persistent', tier.id, 'remove'),
        context: tier.name,
      }),
    })
  
  const stageTierEndpointWeight = (tier: Tier, tierEndpointId: string, weight: number, scope: TierScope) => {
    const updates = rebalanceTierWeights(tier, tierEndpointId, weight, pendingWeights)
    if (!updates.length) return
    setPendingWeights((prev) => {
      const next = { ...prev }
      updates.forEach((entry) => {
        next[entry.id] = entry.weight
      })
      return next
    })
    const key = `${scope}:${tier.id}`
    stagedWeightsRef.current[key] = { scope, updates }
    setDirtyTierIds((prev) => {
      const next = new Set(prev)
      next.add(key)
      return next
    })
  }
  
  const commitTierEndpointWeights = (tier: Tier, scope: TierScope) => {
    const key = `${scope}:${tier.id}`
    const staged = stagedWeightsRef.current[key]
    if (!staged) return
    delete stagedWeightsRef.current[key]
    setSavingTierIds((prev) => {
      const next = new Set(prev)
      next.add(key)
      return next
    })
    const mutation = () => {
      const normalized: Record<string, number> = ensureServerUnits(
        staged.updates.map((entry) => ({ id: entry.id, unit: entry.weight })),
      )
      const ops = staged.updates.map((entry) => {
        const payload = { weight: encodeServerWeight(normalized[entry.id] ?? entry.weight) }
        return updateTierEndpointByScope[scope](entry.id, payload)
      })
      return Promise.all(ops)
    }
    return runMutation(mutation, {
      label: 'Saving weights…',
      busyKey: actionKey('tier', tier.id, 'weights'),
      context: `${tier.name} weights`,
    }).finally(() => {
      setSavingTierIds((prev) => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
      setDirtyTierIds((prev) => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
    })
  }
  
  const handleTierEndpointRemove = (tier: Tier, endpoint: TierEndpoint, scope: TierScope) =>
    confirmDestructiveAction({
      title: `Remove "${endpoint.label}" from ${tier.name}?`,
      message: 'This tier will lose access to the endpoint until it is added again.',
      confirmLabel: 'Remove endpoint',
      onConfirm: () => {
        return runMutation(() => deleteTierEndpointByScope[scope](endpoint.id), {
          successMessage: 'Endpoint removed',
          label: 'Removing endpoint…',
          busyKey: actionKey('tier-endpoint', endpoint.id, 'remove'),
          context: tier.name,
        })
      },
    })
  
  const handleTierEndpointReasoning = (tier: Tier, endpoint: TierEndpoint, value: string | null, scope: TierScope) => {
    if (scope !== 'persistent') return
    const payload: Record<string, unknown> = { reasoning_effort_override: value || null }
    const busyKey = selectedProfile ? actionKey('profile-tier-endpoint', endpoint.id, 'reasoning') : actionKey('tier-endpoint', endpoint.id, 'reasoning')
    const context = tier.name
    if (selectedProfile) {
      return runWithFeedback(
        async () => {
          await llmApi.updateProfilePersistentTierEndpoint(endpoint.id, payload)
          await invalidateProfileDetail()
        },
        {
          label: 'Saving reasoning…',
          busyKey,
          context,
        },
      )
    }
    return runMutation(() => llmApi.updatePersistentTierEndpoint(endpoint.id, payload), {
      label: 'Saving reasoning…',
      busyKey,
      context,
    })
  }
  
  const handleTierEndpointExtraction = (tier: Tier, endpoint: TierEndpoint, extractionId: string | null, scope: TierScope) => {
    if (scope !== 'browser') return
    const payload: Record<string, unknown> = { extraction_endpoint_id: extractionId || null }
    const busyKey = actionKey('tier-endpoint', endpoint.id, 'extraction')
    return runMutation(() => llmApi.updateBrowserTierEndpoint(endpoint.id, payload), {
      label: 'Saving extraction…',
      busyKey,
      context: tier.name,
    })
  }
  
  const handleBrowserTierAdd = (intelligenceTierKey: string) =>
    runMutation(() => llmApi.createBrowserTier({ intelligence_tier: intelligenceTierKey }), {
      successMessage: 'Browser tier added',
      label: 'Creating browser tier…',
      busyKey: actionKey('browser', `${intelligenceTierKey}-add`),
      context: 'Browser tiers',
    })
  const handleBrowserTierMove = (tierId: string, direction: 'up' | 'down') =>
    runMutation(() => llmApi.updateBrowserTier(tierId, { move: direction }), {
      label: direction === 'up' ? 'Moving browser tier up…' : 'Moving browser tier down…',
      busyKey: actionKey('browser', tierId, 'move', direction),
      busyKeys: [actionKey('browser', tierId, 'move')],
      context: 'Browser tiers',
    })
  const handleBrowserTierRemove = (tier: Tier) =>
    confirmDestructiveAction({
      title: `Delete browser tier "${tier.name}"?`,
      message: 'Endpoints assigned to this tier will stop serving browser workloads.',
      confirmLabel: 'Delete tier',
      onConfirm: () => runMutation(() => llmApi.deleteBrowserTier(tier.id), {
        successMessage: 'Browser tier removed',
        label: 'Removing browser tier…',
        busyKey: actionKey('browser', tier.id, 'remove'),
        context: tier.name,
      }),
    })
  
  const handleEmbeddingTierAdd = () => runMutation(() => llmApi.createEmbeddingTier({}), {
    successMessage: 'Embedding tier added',
    label: 'Creating embedding tier…',
    busyKey: actionKey('embedding', 'add'),
    context: 'Embedding tiers',
  })
  const handleEmbeddingTierMove = (tierId: string, direction: 'up' | 'down') =>
    runMutation(() => llmApi.updateEmbeddingTier(tierId, { move: direction }), {
      label: direction === 'up' ? 'Moving embedding tier up…' : 'Moving embedding tier down…',
      busyKey: actionKey('embedding', tierId, 'move', direction),
      busyKeys: [actionKey('embedding', tierId, 'move')],
      context: 'Embedding tiers',
    })
  const handleEmbeddingTierRemove = (tier: Tier) =>
    confirmDestructiveAction({
      title: `Delete embedding tier "${tier.name}"?`,
      message: 'Any weighting rules tied to this tier will be lost.',
      confirmLabel: 'Delete tier',
      onConfirm: () => runMutation(() => llmApi.deleteEmbeddingTier(tier.id), {
        successMessage: 'Embedding tier removed',
        label: 'Removing embedding tier…',
        busyKey: actionKey('embedding', tier.id, 'remove'),
        context: tier.name,
      }),
    })
  
  const handleFileHandlerTierAdd = () => runMutation(() => llmApi.createFileHandlerTier({}), {
    successMessage: 'File handler tier added',
    label: 'Creating file handler tier…',
    busyKey: actionKey('file_handler', 'add'),
    context: 'File handler tiers',
  })
  const handleFileHandlerTierMove = (tierId: string, direction: 'up' | 'down') =>
    runMutation(() => llmApi.updateFileHandlerTier(tierId, { move: direction }), {
      label: direction === 'up' ? 'Moving file handler tier up…' : 'Moving file handler tier down…',
      busyKey: actionKey('file_handler', tierId, 'move', direction),
      busyKeys: [actionKey('file_handler', tierId, 'move')],
      context: 'File handler tiers',
    })
  const handleFileHandlerTierRemove = (tier: Tier) =>
    confirmDestructiveAction({
      title: `Delete file handler tier "${tier.name}"?`,
      message: 'Any weighting rules tied to this tier will be lost.',
      confirmLabel: 'Delete tier',
      onConfirm: () => runMutation(() => llmApi.deleteFileHandlerTier(tier.id), {
        successMessage: 'File handler tier removed',
        label: 'Removing file handler tier…',
        busyKey: actionKey('file_handler', tier.id, 'remove'),
        context: tier.name,
      }),
    })
  
  const imageGenerationActionKey = (useCase: ImageGenerationUseCase, ...parts: Array<string | number>) =>
    actionKey('image_generation', useCase, ...parts)
  
  const handleImageGenerationTierAdd = (useCase: ImageGenerationUseCase) => {
    const config = IMAGE_GENERATION_SECTION_CONFIG[useCase]
    return runMutation(() => llmApi.createImageGenerationTier({ use_case: useCase }), {
      successMessage: config.addSuccessMessage,
      label: config.addLabel,
      busyKey: imageGenerationActionKey(useCase, 'add'),
      context: config.addContext,
    })
  }
  
  const handleImageGenerationTierMove = (
    useCase: ImageGenerationUseCase,
    tierId: string,
    direction: 'up' | 'down',
  ) => {
    const config = IMAGE_GENERATION_SECTION_CONFIG[useCase]
    return runMutation(() => llmApi.updateImageGenerationTier(tierId, { move: direction }), {
      label: direction === 'up' ? config.moveUpLabel : config.moveDownLabel,
      busyKey: imageGenerationActionKey(useCase, tierId, 'move', direction),
      busyKeys: [imageGenerationActionKey(useCase, tierId, 'move')],
      context: config.moveContext,
    })
  }
  
  const handleImageGenerationTierRemove = (useCase: ImageGenerationUseCase, tier: Tier) => {
    const config = IMAGE_GENERATION_SECTION_CONFIG[useCase]
    return confirmDestructiveAction({
      title: `Delete ${config.title.toLowerCase().slice(0, -1)} "${tier.name}"?`,
      message: config.removeMessage,
      confirmLabel: 'Delete tier',
      onConfirm: () => runMutation(() => llmApi.deleteImageGenerationTier(tier.id), {
        successMessage: config.removeSuccessMessage,
        label: config.removeLabel,
        busyKey: imageGenerationActionKey(useCase, tier.id, 'remove'),
        context: tier.name,
      }),
    })
  }
  
  const videoGenerationActionKey = (useCase: VideoGenerationUseCase, ...parts: Array<string | number>) =>
    actionKey('video_generation', useCase, ...parts)
  
  const handleVideoGenerationTierAdd = (useCase: VideoGenerationUseCase) => {
    const config = VIDEO_GENERATION_SECTION_CONFIG[useCase]
    return runMutation(() => llmApi.createVideoGenerationTier({ use_case: useCase }), {
      successMessage: config.addSuccessMessage,
      label: config.addLabel,
      busyKey: videoGenerationActionKey(useCase, 'add'),
      context: config.addContext,
    })
  }
  
  const handleVideoGenerationTierMove = (
    useCase: VideoGenerationUseCase,
    tierId: string,
    direction: 'up' | 'down',
  ) => {
    const config = VIDEO_GENERATION_SECTION_CONFIG[useCase]
    return runMutation(() => llmApi.updateVideoGenerationTier(tierId, { move: direction }), {
      label: direction === 'up' ? config.moveUpLabel : config.moveDownLabel,
      busyKey: videoGenerationActionKey(useCase, tierId, 'move', direction),
      busyKeys: [videoGenerationActionKey(useCase, tierId, 'move')],
      context: config.moveContext,
    })
  }
  
  const handleVideoGenerationTierRemove = (useCase: VideoGenerationUseCase, tier: Tier) => {
    const config = VIDEO_GENERATION_SECTION_CONFIG[useCase]
    return confirmDestructiveAction({
      title: `Delete ${config.title.toLowerCase().slice(0, -1)} "${tier.name}"?`,
      message: config.removeMessage,
      confirmLabel: 'Delete tier',
      onConfirm: () => runMutation(() => llmApi.deleteVideoGenerationTier(tier.id), {
        successMessage: config.removeSuccessMessage,
        label: config.removeLabel,
        busyKey: videoGenerationActionKey(useCase, tier.id, 'remove'),
        context: tier.name,
      }),
    })
  }
  
  const handleTierEndpointAdd = (tier: Tier, scope: TierScope) => {
    const useProfile = Boolean(
      selectedProfile && scope !== 'file_handler' && scope !== 'image_generation' && scope !== 'video_generation',
    )
    showModal((onClose) => createPortal(
      <AddEndpointModal
        tier={tier}
        scope={scope}
        choices={endpointChoices}
        busy={isBusy(actionKey(useProfile ? 'profile' : scope, scope, tier.id, 'attach-endpoint'))}
        onAdd={(selection) => (useProfile ? submitProfileTierEndpoint(tier, scope, selection) : submitTierEndpoint(tier, scope, selection))}
        onClose={onClose}
      />,
      document.body,
    ))
  }
  
  const submitTierEndpoint = async (
    tier: Tier,
    scope: TierScope,
    selection: { endpointId: string; extractionEndpointId?: string | null },
  ) => {
    const { endpointId, extractionEndpointId } = selection
    let stagedWeights: Record<string, number> | null = null
    const mutation = async () => {
      const initialUnit = tier.endpoints.length === 0 ? 1 : MIN_SERVER_UNIT
      const basePayload: { endpoint_id: string; weight: number } = {
        endpoint_id: endpointId,
        weight: encodeServerWeight(initialUnit),
      }
      const response = await addTierEndpointByScope[scope](tier.id, basePayload, extractionEndpointId)
      const newTierEndpointId = response?.tier_endpoint_id
      if (!newTierEndpointId) {
        return
      }
  
      const evenWeights = distributeEvenWeights([...tier.endpoints.map((endpoint) => endpoint.id), newTierEndpointId])
      stagedWeights = evenWeights
      setPendingWeights((prev) => {
        const next = { ...prev }
        Object.entries(evenWeights).forEach(([tierEndpointId, weight]) => {
          next[tierEndpointId] = weight
        })
        return next
      })
      const normalized: Record<string, number> = ensureServerUnits(
        Object.entries(evenWeights).map(([id, unit]) => ({ id, unit })),
      )
      const updates = Object.entries(evenWeights).map(([tierEndpointId, weight]) => {
        const payload = { weight: encodeServerWeight(normalized[tierEndpointId] ?? weight) }
        return updateTierEndpointByScope[scope](tierEndpointId, payload)
      })
  
      await Promise.all(updates)
    }
  
    const busyKey = actionKey(scope, tier.id, 'attach-endpoint')
    try {
      await runMutation(mutation, {
        successMessage: 'Endpoint added',
        label: 'Adding endpoint…',
        busyKey,
        context: tier.name,
        rethrow: true,
      })
    } catch (error) {
      setPendingWeights((prev) => {
        const next = { ...prev }
        if (stagedWeights) {
          Object.keys(stagedWeights).forEach((key) => {
            delete next[key]
          })
        }
        return next
      })
      throw error
    }
  }
  
  // ===============================
  // Profile Management Handlers
  // ===============================
  
  const handleCreateProfile = async (name: string, displayName?: string) => {
    return runWithFeedback(
      async () => {
        const result = await llmApi.createRoutingProfile({
          name: name.toLowerCase().replace(/\s+/g, '-'),
          display_name: displayName || name,
        })
        await invalidateProfiles()
        if (result.profile_id) {
          setSelectedProfileId(result.profile_id)
        }
        return result
      },
      {
        successMessage: 'Profile created',
        label: 'Creating profile…',
        busyKey: 'profile-create',
        context: name,
      },
    )
  }
  
  const openCreateProfileModal = () => {
    showModal((onClose) =>
      <CreateProfileModal
        onCreate={(name) => handleCreateProfile(name)}
        onClose={onClose}
      />,
    )
  }
  
  const handleCloneProfile = async (profileId: string, newName?: string) => {
    return runWithFeedback(
      async () => {
        const result = await llmApi.cloneRoutingProfile(profileId, newName ? { name: newName } : undefined)
        await invalidateProfiles()
        if (result.profile_id) {
          setSelectedProfileId(result.profile_id)
        }
        return result
      },
      {
        successMessage: 'Profile cloned',
        label: 'Cloning profile…',
        busyKey: actionKey('profile', profileId, 'clone'),
        context: 'Routing profile',
      },
    )
  }
  
  const handleActivateProfile = async (profileId: string) => {
    return runWithFeedback(
      async () => {
        await llmApi.activateRoutingProfile(profileId)
        await invalidateProfiles()
        await invalidateProfileDetail()
      },
      {
        successMessage: 'Profile activated',
        label: 'Activating profile…',
        busyKey: actionKey('profile', profileId, 'activate'),
        context: 'Routing profile',
      },
    )
  }
  
  const handleDeleteProfile = (profileId: string, profileName: string) =>
    confirmDestructiveAction({
      title: `Delete profile "${profileName}"?`,
      message: 'This will permanently remove the profile and all its tier configurations. Active profiles cannot be deleted.',
      confirmLabel: 'Delete profile',
      onConfirm: async () => {
        await runWithFeedback(
          async () => {
            await llmApi.deleteRoutingProfile(profileId)
            await invalidateProfiles()
            // If we deleted the selected profile, select the first available
            if (profileId === selectedProfileId) {
              const remaining = profiles.filter(p => p.id !== profileId)
              const next = remaining.find(p => p.is_active) || remaining[0]
              setSelectedProfileId(next?.id || null)
            }
          },
          {
            successMessage: 'Profile deleted',
            label: 'Deleting profile…',
            busyKey: actionKey('profile', profileId, 'delete'),
            context: profileName,
          },
        )
      },
    })
  
  const handleUpdateProfile = async (
    profileId: string,
    payload: {
      display_name?: string
      description?: string
      eval_judge_endpoint_id?: string | null
      summarization_endpoint_id?: string | null
      agent_judge_endpoint_id?: string | null
    },
  ) => {
    return runWithFeedback(
      async () => {
        await llmApi.updateRoutingProfile(profileId, payload)
        await invalidateProfiles()
        await invalidateProfileDetail()
      },
      {
        successMessage: 'Profile updated',
        label: 'Updating profile…',
        busyKey: actionKey('profile', profileId, 'update'),
        context: 'Routing profile',
      },
    )
  }
  
  const openEditProfileModal = (profile: typeof selectedProfile) => {
    if (!profile) return
    showModal((onClose) =>
      <EditProfileModal
        profile={{
          id: profile.id,
          display_name: profile.display_name,
          name: profile.name,
          description: profile.description,
        }}
        onSave={(payload) => handleUpdateProfile(profile.id, payload)}
        onClose={onClose}
      />,
    )
  }
  
  const handleUpdateEvalJudge = async (endpointId: string | null) => {
    if (!selectedProfileId) return
    return runWithFeedback(
      async () => {
        await llmApi.updateRoutingProfile(selectedProfileId, { eval_judge_endpoint_id: endpointId })
        await invalidateProfiles()
        await invalidateProfileDetail()
      },
      {
        successMessage: endpointId ? 'Eval judge updated' : 'Eval judge cleared',
        label: 'Updating eval judge…',
        busyKey: actionKey('profile', selectedProfileId, 'eval-judge'),
        context: 'Eval judge',
      },
    )
  }
  
  const handleUpdateSummarizationEndpoint = async (endpointId: string | null) => {
    if (!selectedProfileId) return
    return runWithFeedback(
      async () => {
        await llmApi.updateRoutingProfile(selectedProfileId, { summarization_endpoint_id: endpointId })
        await invalidateProfiles()
        await invalidateProfileDetail()
      },
      {
        successMessage: endpointId ? 'Summarization model updated' : 'Summarization model cleared',
        label: 'Updating summarization model…',
        busyKey: actionKey('profile', selectedProfileId, 'summarization'),
        context: 'Summarization model',
      },
    )
  }
  
  const handleUpdateAgentJudgeEndpoint = async (endpointId: string | null) => {
    if (!selectedProfileId) return
    return runWithFeedback(
      async () => {
        await llmApi.updateRoutingProfile(selectedProfileId, { agent_judge_endpoint_id: endpointId })
        await invalidateProfiles()
        await invalidateProfileDetail()
      },
      {
        successMessage: endpointId ? 'Agent judge model updated' : 'Agent judge model cleared',
        label: 'Updating agent judge model…',
        busyKey: actionKey('profile', selectedProfileId, 'agent-judge'),
        context: 'Agent judge model',
      },
    )
  }
  
  // ===============================
  // Profile-Specific Tier Handlers
  // ===============================
  
  const handleProfileRangeAdd = () => {
    if (!selectedProfileId) return handleAddRange()
    const sorted = [...persistentStructures.ranges].sort((a, b) => (a.max_tokens ?? Infinity) - (b.max_tokens ?? Infinity))
    const last = sorted.at(-1)
    const baseMin = last?.max_tokens ?? 0
    const name = `Range ${sorted.length + 1}`
    return runWithFeedback(
      async () => {
        await llmApi.createProfileTokenRange(selectedProfileId, { name, min_tokens: baseMin, max_tokens: baseMin + 10000 })
        await invalidateProfileDetail()
      },
      {
        successMessage: 'Range added',
        label: 'Creating range…',
        busyKey: actionKey('profile-range', 'create'),
        context: name,
      },
    )
  }
  
  const handleProfileRangeUpdate = (rangeId: string, field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => {
    if (!selectedProfile) return handleRangeUpdate(rangeId, field, value)
    const payload: Record<string, string | number | null> = {}
    payload[field] = value
    return runWithFeedback(
      async () => {
        await llmApi.updateProfileTokenRange(rangeId, payload)
        await invalidateProfileDetail()
      },
      {
        label: 'Saving range…',
        busyKey: actionKey('profile-range', rangeId, field),
        context: 'Token range',
      },
    )
  }
  
  const handleProfileRangeRemove = (range: TokenRange) => {
    if (!selectedProfile) return handleRangeRemove(range)
    return confirmDestructiveAction({
      title: `Delete range "${range.name}"?`,
      message: 'All tiers in this token range will also be deleted.',
      confirmLabel: 'Delete range',
      onConfirm: () =>
        runWithFeedback(
          async () => {
            await llmApi.deleteProfileTokenRange(range.id)
            await invalidateProfileDetail()
          },
          {
            successMessage: 'Range removed',
            label: 'Removing range…',
            busyKey: actionKey('profile-range', range.id, 'remove'),
            context: range.name,
          },
        ),
    })
  }
  
  const handleProfileTierAdd = (rangeId: string, intelligenceTierKey: string) => {
    if (!selectedProfile) return handleTierAdd(rangeId, intelligenceTierKey)
    return runWithFeedback(
      async () => {
        await llmApi.createProfilePersistentTier(rangeId, { intelligence_tier: intelligenceTierKey })
        await invalidateProfileDetail()
      },
      {
        successMessage: 'Tier added',
        label: 'Creating tier…',
        busyKey: actionKey('profile-range', rangeId, `add-${intelligenceTierKey}-tier`),
        context: 'Persistent tier',
      },
    )
  }
  
  const handleProfileTierMove = (rangeId: string, tierId: string, direction: 'up' | 'down') => {
    if (!selectedProfile) return handleTierMove(rangeId, tierId, direction)
    return runWithFeedback(
      async () => {
        await llmApi.updateProfilePersistentTier(tierId, { move: direction })
        await invalidateProfileDetail()
      },
      {
        label: direction === 'up' ? 'Moving tier up…' : 'Moving tier down…',
        busyKey: actionKey('profile-persistent', tierId, 'move', direction),
        busyKeys: [actionKey('profile-persistent', tierId, 'move'), actionKey('profile-persistent-range', rangeId, 'move')],
        context: 'Persistent tier',
      },
    )
  }
  
  const handleProfileTierRemove = (tier: Tier) => {
    if (!selectedProfile) return handleTierRemove(tier)
    return confirmDestructiveAction({
      title: `Delete tier "${tier.name}"?`,
      message: 'Endpoints will be detached from this tier.',
      confirmLabel: 'Delete tier',
      onConfirm: () =>
        runWithFeedback(
          async () => {
            await llmApi.deleteProfilePersistentTier(tier.id)
            await invalidateProfileDetail()
          },
          {
            successMessage: 'Tier removed',
            label: 'Removing tier…',
            busyKey: actionKey('profile-persistent', tier.id, 'remove'),
            context: tier.name,
          },
        ),
    })
  }
  
  const handleProfileBrowserTierAdd = (intelligenceTierKey: string) => {
    if (!selectedProfile || !selectedProfileId) return handleBrowserTierAdd(intelligenceTierKey)
    return runWithFeedback(
      async () => {
        await llmApi.createProfileBrowserTier(selectedProfileId, { intelligence_tier: intelligenceTierKey })
        await invalidateProfileDetail()
      },
      {
        successMessage: 'Browser tier added',
        label: 'Creating browser tier…',
        busyKey: actionKey('profile-browser', `${intelligenceTierKey}-add`),
        context: 'Browser tiers',
      },
    )
  }
  
  const handleProfileBrowserTierMove = (tierId: string, direction: 'up' | 'down') => {
    if (!selectedProfile) return handleBrowserTierMove(tierId, direction)
    return runWithFeedback(
      async () => {
        await llmApi.updateProfileBrowserTier(tierId, { move: direction })
        await invalidateProfileDetail()
      },
      {
        label: direction === 'up' ? 'Moving browser tier up…' : 'Moving browser tier down…',
        busyKey: actionKey('profile-browser', tierId, 'move', direction),
        busyKeys: [actionKey('profile-browser', tierId, 'move')],
        context: 'Browser tiers',
      },
    )
  }
  
  const handleProfileBrowserTierRemove = (tier: Tier) => {
    if (!selectedProfile) return handleBrowserTierRemove(tier)
    return confirmDestructiveAction({
      title: `Delete browser tier "${tier.name}"?`,
      message: 'Endpoints assigned to this tier will stop serving browser workloads.',
      confirmLabel: 'Delete tier',
      onConfirm: () =>
        runWithFeedback(
          async () => {
            await llmApi.deleteProfileBrowserTier(tier.id)
            await invalidateProfileDetail()
          },
          {
            successMessage: 'Browser tier removed',
            label: 'Removing browser tier…',
            busyKey: actionKey('profile-browser', tier.id, 'remove'),
            context: tier.name,
          },
        ),
    })
  }
  
  const handleProfileEmbeddingTierAdd = () => {
    if (!selectedProfile || !selectedProfileId) return handleEmbeddingTierAdd()
    return runWithFeedback(
      async () => {
        await llmApi.createProfileEmbeddingTier(selectedProfileId, {})
        await invalidateProfileDetail()
      },
      {
        successMessage: 'Embedding tier added',
        label: 'Creating embedding tier…',
        busyKey: actionKey('profile-embedding', 'add'),
        context: 'Embedding tiers',
      },
    )
  }
  
  const handleProfileEmbeddingTierMove = (tierId: string, direction: 'up' | 'down') => {
    if (!selectedProfile) return handleEmbeddingTierMove(tierId, direction)
    return runWithFeedback(
      async () => {
        await llmApi.updateProfileEmbeddingTier(tierId, { move: direction })
        await invalidateProfileDetail()
      },
      {
        label: direction === 'up' ? 'Moving embedding tier up…' : 'Moving embedding tier down…',
        busyKey: actionKey('profile-embedding', tierId, 'move', direction),
        busyKeys: [actionKey('profile-embedding', tierId, 'move')],
        context: 'Embedding tiers',
      },
    )
  }
  
  const handleProfileEmbeddingTierRemove = (tier: Tier) => {
    if (!selectedProfile) return handleEmbeddingTierRemove(tier)
    return confirmDestructiveAction({
      title: `Delete embedding tier "${tier.name}"?`,
      message: 'Any weighting rules tied to this tier will be lost.',
      confirmLabel: 'Delete tier',
      onConfirm: () =>
        runWithFeedback(
          async () => {
            await llmApi.deleteProfileEmbeddingTier(tier.id)
            await invalidateProfileDetail()
          },
          {
            successMessage: 'Embedding tier removed',
            label: 'Removing embedding tier…',
            busyKey: actionKey('profile-embedding', tier.id, 'remove'),
            context: tier.name,
          },
        ),
    })
  }
  
  // Profile-specific tier endpoint handlers
  const commitProfileTierEndpointWeights = (tier: Tier, scope: TierScope) => {
    if (!selectedProfile) return commitTierEndpointWeights(tier, scope)
    if (scope === 'file_handler' || scope === 'image_generation' || scope === 'video_generation') return
  
    const key = `${scope}:${tier.id}`
    const staged = stagedWeightsRef.current[key]
    if (!staged) return
    delete stagedWeightsRef.current[key]
    setSavingTierIds((prev) => {
      const next = new Set(prev)
      next.add(key)
      return next
    })
    const mutation = async () => {
      const normalized: Record<string, number> = ensureServerUnits(
        staged.updates.map((entry) => ({ id: entry.id, unit: entry.weight })),
      )
      const ops = staged.updates.map((entry) => {
        const payload = { weight: encodeServerWeight(normalized[entry.id] ?? entry.weight) }
        return updateProfileTierEndpointByScope[scope](entry.id, payload)
      })
      await Promise.all(ops)
      await invalidateProfileDetail()
    }
    return runWithFeedback(mutation, {
      label: 'Saving weights…',
      busyKey: actionKey('profile-tier', tier.id, 'weights'),
      context: `${tier.name} weights`,
    }).finally(() => {
      setSavingTierIds((prev) => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
      setDirtyTierIds((prev) => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
    })
  }
  
  const handleProfileTierEndpointRemove = (tier: Tier, endpoint: TierEndpoint, scope: TierScope) => {
    if (!selectedProfile) return handleTierEndpointRemove(tier, endpoint, scope)
    if (scope === 'file_handler' || scope === 'image_generation' || scope === 'video_generation') return
  
    return confirmDestructiveAction({
      title: `Remove "${endpoint.label}" from ${tier.name}?`,
      message: 'This tier will lose access to the endpoint until it is added again.',
      confirmLabel: 'Remove endpoint',
      onConfirm: () =>
        runWithFeedback(
          async () => {
            await deleteProfileTierEndpointByScope[scope](endpoint.id)
            await invalidateProfileDetail()
          },
          {
            successMessage: 'Endpoint removed',
            label: 'Removing endpoint…',
            busyKey: actionKey('profile-tier-endpoint', endpoint.id, 'remove'),
            context: tier.name,
          },
        ),
    })
  }
  
  const submitProfileTierEndpoint = async (
    tier: Tier,
    scope: TierScope,
    selection: { endpointId: string; extractionEndpointId?: string | null },
  ) => {
    if (!selectedProfile) return submitTierEndpoint(tier, scope, selection)
    if (scope === 'file_handler' || scope === 'image_generation' || scope === 'video_generation') return
    const { endpointId, extractionEndpointId } = selection
    let stagedWeights: Record<string, number> | null = null
    const mutation = async () => {
      const initialUnit = tier.endpoints.length === 0 ? 1 : MIN_SERVER_UNIT
      const basePayload: { endpoint_id: string; weight: number } = {
        endpoint_id: endpointId,
        weight: encodeServerWeight(initialUnit),
      }
      const response = await addProfileTierEndpointByScope[scope](tier.id, basePayload, extractionEndpointId)
      const newTierEndpointId = response?.tier_endpoint_id
      if (!newTierEndpointId) {
        return
      }
  
      const evenWeights = distributeEvenWeights([...tier.endpoints.map((ep) => ep.id), newTierEndpointId])
      stagedWeights = evenWeights
      setPendingWeights((prev) => {
        const next = { ...prev }
        Object.entries(evenWeights).forEach(([tierEndpointId, weight]) => {
          next[tierEndpointId] = weight
        })
        return next
      })
  
      const normalized: Record<string, number> = ensureServerUnits(
        Object.entries(evenWeights).map(([id, unit]) => ({ id, unit })),
      )
      const updates = Object.entries(evenWeights).map(([tierEndpointId, weight]) => {
        const payload = { weight: encodeServerWeight(normalized[tierEndpointId] ?? weight) }
        return updateProfileTierEndpointByScope[scope](tierEndpointId, payload)
      })
  
      await Promise.all(updates)
      await invalidateProfileDetail()
    }
  
    const busyKey = actionKey('profile', scope, tier.id, 'attach-endpoint')
    try {
      await runWithFeedback(mutation, {
        successMessage: 'Endpoint added',
        label: 'Adding endpoint…',
        busyKey,
        context: tier.name,
      })
    } catch (error) {
      setPendingWeights((prev) => {
        const next = { ...prev }
        if (stagedWeights) {
          Object.keys(stagedWeights).forEach((key) => {
            delete next[key]
          })
        }
        return next
      })
      throw error
    }
  }
  
  const handleProfileTierEndpointExtraction = (tier: Tier, endpoint: TierEndpoint, extractionId: string | null, scope: TierScope) => {
    if (!selectedProfile || scope !== 'browser') return handleTierEndpointExtraction(tier, endpoint, extractionId, scope)
    const payload: Record<string, unknown> = { extraction_endpoint_id: extractionId || null }
    const busyKey = actionKey('profile-tier-endpoint', endpoint.id, 'extraction')
    return runWithFeedback(
      async () => {
        await llmApi.updateProfileBrowserTierEndpoint(endpoint.id, payload)
        await invalidateProfileDetail()
      },
      {
        label: 'Saving extraction…',
        busyKey,
        context: tier.name,
      },
    )
  }
  
  return {
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
  }

}
