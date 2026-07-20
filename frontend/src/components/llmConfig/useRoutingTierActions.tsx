import { useEffect, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'

import * as llmApi from '../../api/llmConfig'
import { AddEndpointModal, CreateProfileModal, EditProfileModal } from './modals'
import {
  actionKey,
  distributeEvenWeights,
  encodeServerWeight,
  ensureServerUnits,
  IMAGE_GENERATION_SECTION_CONFIG,
  MIN_SERVER_UNIT,
  rebalanceTierWeights,
  VIDEO_GENERATION_SECTION_CONFIG,
  type AsyncFeedback,
  type ConfirmDialogConfig,
  type ImageGenerationUseCase,
  type MutationOptions,
  type Tier,
  type TierEndpoint,
  type TierScope,
  type TokenRange,
  type VideoGenerationUseCase,
} from './shared'

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

  const routingClient = llmApi.createRoutingConfigClient(selectedProfile?.id ?? null)
  const isRoutingScope = (scope: TierScope): scope is llmApi.RoutingTierScope => (
    scope === 'persistent' || scope === 'browser' || scope === 'embedding'
  )
  const getTierClient = (scope: TierScope) => (
    routingClient.isProfile && isRoutingScope(scope)
      ? routingClient.tiers[scope]
      : llmApi.systemTierClients[scope]
  )
  const runRoutingMutation = async <T,>(action: () => Promise<T>, options?: MutationOptions): Promise<void> => {
    if (!routingClient.isProfile) return runMutation(action, options)
    await runWithFeedback(async () => {
      await action()
      await invalidateProfileDetail()
    }, options)
  }
  const routingKey = (scope: llmApi.RoutingTierScope | 'range', ...parts: Array<string | number>) => (
    actionKey(routingClient.isProfile ? `profile-${scope}` : scope, ...parts)
  )

  const handleRangeUpdate = (rangeId: string, field: 'name' | 'min_tokens' | 'max_tokens', value: string | number | null) => {
    return runRoutingMutation(() => routingClient.ranges.update(rangeId, { [field]: value }), {
      label: 'Saving range…',
      busyKey: routingKey('range', rangeId, field),
      context: 'Token range',
      rethrow: true,
    })
  }
  
  const handleAddRange = () => {
    const sorted = [...persistentStructures.ranges].sort((a, b) => (a.max_tokens ?? Infinity) - (b.max_tokens ?? Infinity))
    const last = sorted.at(-1)
    const baseMin = last?.max_tokens ?? 0
    const name = `Range ${sorted.length + 1}`
    return runRoutingMutation(
      () => routingClient.ranges.create({ name, min_tokens: baseMin, max_tokens: baseMin + 10000 }),
      {
        successMessage: 'Range added',
        label: 'Creating range…',
        busyKey: routingKey('range', 'create'),
        context: name,
      },
    )
  }
  
  const handleRangeRemove = (range: TokenRange) =>
    confirmDestructiveAction({
      title: `Delete range "${range.name}"?`,
      message: routingClient.isProfile
        ? 'All tiers in this token range will also be deleted.'
        : 'All tiers in this token range will also be deleted. Provider endpoints remain available for reuse.',
      confirmLabel: 'Delete range',
      onConfirm: () =>
        runRoutingMutation(() => routingClient.ranges.remove(range.id), {
          successMessage: 'Range removed',
          label: 'Removing range…',
          busyKey: routingKey('range', range.id, 'remove'),
          context: range.name,
        }),
    })
  
  const handleTierAdd = (rangeId: string, intelligenceTierKey: string) => {
    return runRoutingMutation(() => routingClient.tiers.persistent.create(rangeId, { intelligence_tier: intelligenceTierKey }), {
      successMessage: 'Tier added',
      label: 'Creating tier…',
      busyKey: routingKey('range', rangeId, `add-${intelligenceTierKey}-tier`),
      context: 'Persistent tier',
    })
  }
  const handleTierMove = (rangeId: string, tierId: string, direction: 'up' | 'down') =>
    runRoutingMutation(() => routingClient.tiers.persistent.update(tierId, { move: direction }), {
      label: direction === 'up' ? 'Moving tier up…' : 'Moving tier down…',
      busyKey: routingKey('persistent', tierId, 'move', direction),
      busyKeys: [routingKey('persistent', tierId, 'move'), routingKey('persistent', 'range', rangeId, 'move')],
      context: 'Persistent tier',
    })
  const handleTierRemove = (tier: Tier) =>
    confirmDestructiveAction({
      title: `Delete tier "${tier.name}"?`,
      message: routingClient.isProfile
        ? 'Endpoints will be detached from this tier.'
        : 'Endpoints will be detached from this persistent tier.',
      confirmLabel: 'Delete tier',
      onConfirm: () => runRoutingMutation(() => routingClient.tiers.persistent.remove(tier.id), {
        successMessage: 'Tier removed',
        label: 'Removing tier…',
        busyKey: routingKey('persistent', tier.id, 'remove'),
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
        return getTierClient(scope).updateEndpoint(entry.id, payload)
      })
      return Promise.all(ops)
    }
    return runRoutingMutation(mutation, {
      label: 'Saving weights…',
      busyKey: actionKey(routingClient.isProfile ? 'profile-tier' : 'tier', tier.id, 'weights'),
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
        return runRoutingMutation(() => getTierClient(scope).removeEndpoint(endpoint.id), {
          successMessage: 'Endpoint removed',
          label: 'Removing endpoint…',
          busyKey: actionKey(routingClient.isProfile ? 'profile-tier-endpoint' : 'tier-endpoint', endpoint.id, 'remove'),
          context: tier.name,
        })
      },
    })
  
  const handleTierEndpointReasoning = (tier: Tier, endpoint: TierEndpoint, value: string | null, scope: TierScope) => {
    if (scope !== 'persistent') return
    const payload: Record<string, unknown> = { reasoning_effort_override: value || null }
    return runRoutingMutation(() => routingClient.tiers.persistent.updateEndpoint(endpoint.id, payload), {
      label: 'Saving reasoning…',
      busyKey: actionKey(routingClient.isProfile ? 'profile-tier-endpoint' : 'tier-endpoint', endpoint.id, 'reasoning'),
      context: tier.name,
    })
  }
  
  const handleTierEndpointExtraction = (tier: Tier, endpoint: TierEndpoint, extractionId: string | null, scope: TierScope) => {
    if (scope !== 'browser') return
    const payload: Record<string, unknown> = { extraction_endpoint_id: extractionId || null }
    return runRoutingMutation(() => routingClient.tiers.browser.updateEndpoint(endpoint.id, payload), {
      label: 'Saving extraction…',
      busyKey: actionKey(routingClient.isProfile ? 'profile-tier-endpoint' : 'tier-endpoint', endpoint.id, 'extraction'),
      context: tier.name,
    })
  }
  
  const handleBrowserTierAdd = (intelligenceTierKey: string) =>
    runRoutingMutation(() => routingClient.tiers.browser.create(selectedProfileId, { intelligence_tier: intelligenceTierKey }), {
      successMessage: 'Browser tier added',
      label: 'Creating browser tier…',
      busyKey: routingKey('browser', `${intelligenceTierKey}-add`),
      context: 'Browser tiers',
    })
  const handleBrowserTierMove = (tierId: string, direction: 'up' | 'down') =>
    runRoutingMutation(() => routingClient.tiers.browser.update(tierId, { move: direction }), {
      label: direction === 'up' ? 'Moving browser tier up…' : 'Moving browser tier down…',
      busyKey: routingKey('browser', tierId, 'move', direction),
      busyKeys: [routingKey('browser', tierId, 'move')],
      context: 'Browser tiers',
    })
  const handleBrowserTierRemove = (tier: Tier) =>
    confirmDestructiveAction({
      title: `Delete browser tier "${tier.name}"?`,
      message: 'Endpoints assigned to this tier will stop serving browser workloads.',
      confirmLabel: 'Delete tier',
      onConfirm: () => runRoutingMutation(() => routingClient.tiers.browser.remove(tier.id), {
        successMessage: 'Browser tier removed',
        label: 'Removing browser tier…',
        busyKey: routingKey('browser', tier.id, 'remove'),
        context: tier.name,
      }),
    })
  
  const handleEmbeddingTierAdd = () => runRoutingMutation(() => routingClient.tiers.embedding.create(selectedProfileId, {}), {
    successMessage: 'Embedding tier added',
    label: 'Creating embedding tier…',
    busyKey: routingKey('embedding', 'add'),
    context: 'Embedding tiers',
  })
  const handleEmbeddingTierMove = (tierId: string, direction: 'up' | 'down') =>
    runRoutingMutation(() => routingClient.tiers.embedding.update(tierId, { move: direction }), {
      label: direction === 'up' ? 'Moving embedding tier up…' : 'Moving embedding tier down…',
      busyKey: routingKey('embedding', tierId, 'move', direction),
      busyKeys: [routingKey('embedding', tierId, 'move')],
      context: 'Embedding tiers',
    })
  const handleEmbeddingTierRemove = (tier: Tier) =>
    confirmDestructiveAction({
      title: `Delete embedding tier "${tier.name}"?`,
      message: 'Any weighting rules tied to this tier will be lost.',
      confirmLabel: 'Delete tier',
      onConfirm: () => runRoutingMutation(() => routingClient.tiers.embedding.remove(tier.id), {
        successMessage: 'Embedding tier removed',
        label: 'Removing embedding tier…',
        busyKey: routingKey('embedding', tier.id, 'remove'),
        context: tier.name,
      }),
    })
  
  const handleFileHandlerTierAdd = () => runMutation(() => llmApi.systemTierClients.file_handler.create(null, {}), {
    successMessage: 'File handler tier added',
    label: 'Creating file handler tier…',
    busyKey: actionKey('file_handler', 'add'),
    context: 'File handler tiers',
  })
  const handleFileHandlerTierMove = (tierId: string, direction: 'up' | 'down') =>
    runMutation(() => llmApi.systemTierClients.file_handler.update(tierId, { move: direction }), {
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
      onConfirm: () => runMutation(() => llmApi.systemTierClients.file_handler.remove(tier.id), {
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
    return runMutation(() => llmApi.systemTierClients.image_generation.create(null, { use_case: useCase }), {
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
    return runMutation(() => llmApi.systemTierClients.image_generation.update(tierId, { move: direction }), {
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
      onConfirm: () => runMutation(() => llmApi.systemTierClients.image_generation.remove(tier.id), {
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
    return runMutation(() => llmApi.systemTierClients.video_generation.create(null, { use_case: useCase }), {
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
    return runMutation(() => llmApi.systemTierClients.video_generation.update(tierId, { move: direction }), {
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
      onConfirm: () => runMutation(() => llmApi.systemTierClients.video_generation.remove(tier.id), {
        successMessage: config.removeSuccessMessage,
        label: config.removeLabel,
        busyKey: videoGenerationActionKey(useCase, tier.id, 'remove'),
        context: tier.name,
      }),
    })
  }
  
  const handleTierEndpointAdd = (tier: Tier, scope: TierScope) => {
    showModal((onClose) => (
      <AddEndpointModal
        tier={tier}
        scope={scope}
        choices={endpointChoices}
        busy={isBusy(actionKey(routingClient.isProfile && isRoutingScope(scope) ? 'profile' : scope, scope, tier.id, 'attach-endpoint'))}
        onAdd={(selection) => submitTierEndpoint(tier, scope, selection)}
        onClose={onClose}
      />
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
      const payload: llmApi.TierEndpointCreatePayload = {
        endpoint_id: endpointId,
        weight: encodeServerWeight(initialUnit),
      }
      if (scope === 'browser' && typeof extractionEndpointId !== 'undefined') {
        payload.extraction_endpoint_id = extractionEndpointId || null
      }
      const client = getTierClient(scope)
      const response = await client.addEndpoint(tier.id, payload)
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
        return client.updateEndpoint(tierEndpointId, payload)
      })
  
      await Promise.all(updates)
    }
  
    const busyKey = actionKey(routingClient.isProfile && isRoutingScope(scope) ? 'profile' : scope, scope, tier.id, 'attach-endpoint')
    try {
      await runRoutingMutation(mutation, {
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
  }
}
