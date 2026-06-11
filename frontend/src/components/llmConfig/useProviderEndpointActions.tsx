import { useEffect, useState } from 'react'

import * as llmApi from '../../api/llmConfig'
import { HttpError } from '../../api/http'
import { EndpointDeleteMessage, getEndpointDeleteConflictUsage } from './modals'
import type { ProviderCardHandlers } from './ProviderCard'
import {
  actionKey,
  endpointKindFromType,
  parseNumber,
  type AsyncFeedback,
  type ConfirmDialogConfig,
  type EndpointFormValues,
  type EndpointTestStatus,
  type MutationOptions,
  type ProviderCardData,
  type ProviderEndpointCard,
} from './shared'

type RunMutation = <T>(action: () => Promise<T>, options?: MutationOptions) => Promise<void>

type UseProviderEndpointActionsArgs = {
  providers: ProviderCardData[]
  runMutation: RunMutation
  runWithFeedback: AsyncFeedback['runWithFeedback']
  confirmDestructiveAction: (options: ConfirmDialogConfig) => Promise<void>
  invalidateProfileDetail: () => Promise<unknown>
}

export function useProviderEndpointActions({
  providers,
  runMutation,
  runWithFeedback,
  confirmDestructiveAction,
  invalidateProfileDetail,
}: UseProviderEndpointActionsArgs) {
  const [endpointTestStatuses, setEndpointTestStatuses] = useState<Record<string, EndpointTestStatus>>({})
  const resetEndpointTestStatuses = () => setEndpointTestStatuses({})

  useEffect(() => {
    if (!providers.length) {
      setEndpointTestStatuses({})
      return
    }
    const valid = new Set(providers.flatMap((provider) => provider.endpoints.map((endpoint) => endpoint.id)))
    setEndpointTestStatuses((prev) => {
      const next: Record<string, EndpointTestStatus> = {}
      valid.forEach((id) => {
        if (prev[id]) next[id] = prev[id]
      })
      return next
    })
  }, [providers])

  const promptForKey = (message: string) => {
    const value = window.prompt(message)
    if (!value) return null
    return value.trim()
  }
  
  const handleProviderRotateKey = (provider: ProviderCardData) => {
    const next = promptForKey('Enter the new admin API key')
    if (!next) return Promise.resolve()
    return runMutation(() => llmApi.updateProvider(provider.id, { api_key: next }), {
      successMessage: 'API key updated',
      label: 'Rotating API key…',
      busyKey: actionKey('provider', provider.id, 'rotate'),
      context: provider.name,
      rethrow: true,
    })
  }
  
  const handleProviderClearKey = (provider: ProviderCardData) => {
    return runMutation(() => llmApi.updateProvider(provider.id, { clear_api_key: true }), {
      successMessage: 'Stored API key cleared',
      label: 'Clearing API key…',
      busyKey: actionKey('provider', provider.id, 'clear'),
      context: provider.name,
      rethrow: true,
    })
  }
  
  const handleProviderTestEndpoint = async (endpoint: ProviderEndpointCard) => {
    setEndpointTestStatuses((prev) => ({
      ...prev,
      [endpoint.id]: {
        state: 'pending',
        message: 'Testing…',
        updatedAt: Date.now(),
      },
    }))
    try {
      const result = await runWithFeedback(
        () => llmApi.testEndpoint({ endpoint_id: endpoint.id, kind: endpoint.type }),
        {
          label: 'Testing endpoint…',
          busyKey: actionKey('endpoint', endpoint.id, 'test'),
          context: endpoint.name,
        },
      )
      if (!result.ok) {
        throw new Error(result.message || 'Endpoint test failed')
      }
      setEndpointTestStatuses((prev) => ({
        ...prev,
        [endpoint.id]: {
          state: 'success',
          message: result.message || 'Endpoint responded successfully.',
          preview: result.preview?.trim() || '',
          latencyMs: result.latency_ms ?? null,
          totalTokens: result.total_tokens ?? null,
          promptTokens: result.prompt_tokens ?? null,
          completionTokens: result.completion_tokens ?? null,
          updatedAt: Date.now(),
        },
      }))
    } catch (error) {
      const message = error instanceof HttpError
        ? (typeof error.body === 'object' && error.body && 'message' in error.body ? String((error.body as { message?: unknown }).message || error.message) : error.message)
        : (error as Error).message
      setEndpointTestStatuses((prev) => ({
        ...prev,
        [endpoint.id]: {
          state: 'error',
          message,
          updatedAt: Date.now(),
        },
      }))
      throw error
    }
  }
  
  const handleProviderToggle = (provider: ProviderCardData, enabled: boolean) => {
    return runMutation(
      () => llmApi.updateProvider(provider.id, { enabled }),
      {
        successMessage: enabled ? 'Provider enabled' : 'Provider disabled',
        label: enabled ? 'Enabling provider…' : 'Disabling provider…',
        busyKey: actionKey('provider', provider.id, 'toggle'),
        context: provider.name,
      },
    )
  }
  
  const handleProviderAddEndpoint = (
    provider: ProviderCardData,
    type: llmApi.ProviderEndpoint['type'],
    values: EndpointFormValues & { key: string },
  ) => {
    const kind = endpointKindFromType(type)
    const payload: Record<string, unknown> = {
      provider_id: provider.id,
      key: values.key,
    }
    if (type === 'browser') {
      payload.browser_model = values.model
      payload.model = values.model
      payload.browser_base_url = values.browser_base_url || values.api_base || ''
      const maxTokens = parseNumber(values.max_output_tokens)
      if (maxTokens !== undefined) payload.max_output_tokens = maxTokens
      payload.supports_temperature = values.supportsTemperature ?? true
      payload.supports_vision = Boolean(values.supportsVision)
      payload.enabled = true
    } else if (type === 'embedding') {
      payload.model = values.model
      payload.litellm_model = values.model
      payload.api_base = values.api_base || ''
      payload.enabled = true
    } else if (type === 'file_handler') {
      payload.model = values.model
      payload.litellm_model = values.model
      payload.api_base = values.api_base || ''
      payload.supports_vision = values.supportsVision ?? false
      payload.enabled = true
    } else if (type === 'image_generation') {
      payload.model = values.model
      payload.litellm_model = values.model
      payload.api_base = values.api_base || ''
      payload.supports_image_to_image = values.supportsImageToImage ?? false
      payload.enabled = true
    } else if (type === 'video_generation') {
      payload.model = values.model
      payload.litellm_model = values.model
      payload.api_base = values.api_base || ''
      payload.supports_image_to_video = values.supportsImageToVideo ?? false
      payload.enabled = true
    } else {
      payload.model = values.model
      payload.litellm_model = values.model
      payload.api_base = values.api_base || ''
      const temp = parseNumber(values.temperature)
      payload.temperature_override = temp ?? null
      payload.supports_temperature = values.supportsTemperature ?? true
      payload.supports_tool_choice = values.supportsToolChoice ?? true
      payload.use_parallel_tool_calls = values.useParallelToolCalls ?? true
      payload.allow_implied_send = values.allowImpliedSend ?? true
      payload.supports_vision = values.supportsVision ?? false
      payload.supports_reasoning = values.supportsReasoning ?? false
      payload.reasoning_effort = values.reasoningEffort ? values.reasoningEffort : null
      if (values.openrouterPreset !== undefined) {
        payload.openrouter_preset = values.openrouterPreset.trim()
      }
      const maxInput = parseNumber(values.max_input_tokens)
      if (maxInput !== undefined) payload.max_input_tokens = maxInput
      payload.enabled = true
    }
    if (type !== 'browser') {
      payload.litellm_pricing_model = values.litellm_pricing_model?.trim() || null
    }
    payload.low_latency = values.lowLatency ?? false
    return runMutation(() => llmApi.createEndpoint(kind, payload), {
      successMessage: 'Endpoint added',
      label: 'Creating endpoint…',
      busyKey: actionKey('provider', provider.id, 'create-endpoint'),
      context: provider.name,
      rethrow: true,
    }).then(() => {
      resetEndpointTestStatuses()
    })
  }
  
  const handleProviderSaveEndpoint = (endpoint: ProviderEndpointCard, values: EndpointFormValues) => {
    const kind = endpointKindFromType(endpoint.type)
    const payload: Record<string, unknown> = {}
    if (values.model) {
      payload.model = values.model
      if (kind === 'browser') payload.browser_model = values.model
      if (kind !== 'browser') payload.litellm_model = values.model
    }
    if (kind !== 'browser' && values.litellm_pricing_model !== undefined) {
      payload.litellm_pricing_model = values.litellm_pricing_model.trim() || null
    }
    if (values.api_base) {
      payload.api_base = values.api_base
      if (kind === 'browser') payload.browser_base_url = values.api_base
    }
    if (values.browser_base_url) {
      payload.browser_base_url = values.browser_base_url
    }
    if (kind === 'browser' && values.max_output_tokens !== undefined) {
      const parsed = parseNumber(values.max_output_tokens)
      payload.max_output_tokens = parsed ?? null
    }
    if (kind !== 'browser' && kind !== 'image_generation' && kind !== 'video_generation' && values.temperature !== undefined) {
      const parsed = parseNumber(values.temperature)
      payload.temperature_override = parsed ?? null
    }
    if (kind !== 'image_generation' && kind !== 'video_generation' && values.supportsTemperature !== undefined) payload.supports_temperature = values.supportsTemperature
    if (kind !== 'image_generation' && kind !== 'video_generation' && values.supportsVision !== undefined) payload.supports_vision = values.supportsVision
    if (kind === 'image_generation' && values.supportsImageToImage !== undefined) {
      payload.supports_image_to_image = values.supportsImageToImage
    }
    if (kind === 'video_generation' && values.supportsImageToVideo !== undefined) {
      payload.supports_image_to_video = values.supportsImageToVideo
    }
    if (kind === 'persistent' && values.supportsToolChoice !== undefined) payload.supports_tool_choice = values.supportsToolChoice
    if (kind === 'persistent' && values.useParallelToolCalls !== undefined) payload.use_parallel_tool_calls = values.useParallelToolCalls
    if (kind === 'persistent' && values.allowImpliedSend !== undefined) payload.allow_implied_send = values.allowImpliedSend
    if (values.lowLatency !== undefined) payload.low_latency = values.lowLatency
    if (kind === 'persistent') {
      if (values.supportsReasoning !== undefined) payload.supports_reasoning = values.supportsReasoning
      if (values.reasoningEffort !== undefined) payload.reasoning_effort = values.reasoningEffort || null
      if (values.openrouterPreset !== undefined) payload.openrouter_preset = values.openrouterPreset.trim()
      if (values.max_input_tokens !== undefined) {
        const parsed = parseNumber(values.max_input_tokens)
        payload.max_input_tokens = parsed ?? null
      }
    }
    return runMutation(() => llmApi.updateEndpoint(kind, endpoint.id, payload), {
      successMessage: 'Endpoint updated',
      label: 'Saving endpoint…',
      busyKey: actionKey('endpoint', endpoint.id, 'update'),
      context: endpoint.name,
      rethrow: true,
    }).then(() => {
      resetEndpointTestStatuses()
    })
  }
  
  const handleProviderDeleteEndpoint = (endpoint: ProviderEndpointCard) => {
    const kind = endpointKindFromType(endpoint.type)
    const displayName = endpoint.name || endpoint.api_base || endpoint.browser_base_url || endpoint.id
    const deleteEndpoint = (force: boolean) => runMutation(() => llmApi.deleteEndpoint(kind, endpoint.id, { force }), {
      successMessage: 'Endpoint removed',
      label: 'Removing endpoint…',
      busyKey: actionKey('endpoint', endpoint.id, 'delete'),
      context: endpoint.name,
      rethrow: true,
    }).then(async () => {
      await invalidateProfileDetail()
      resetEndpointTestStatuses()
    })
  
    const confirmDeleteWithUsage = (usage: llmApi.EndpointTierUsage[]) => confirmDestructiveAction({
      title: `Delete endpoint "${displayName}"?`,
      message: <EndpointDeleteMessage usage={usage} />,
      confirmLabel: usage.length ? 'Delete and remove from tiers' : 'Delete endpoint',
      onConfirm: () => deleteEndpoint(usage.length > 0),
    })
  
    return confirmDeleteWithUsage(endpoint.tierUsage).catch((error) => {
      const usage = getEndpointDeleteConflictUsage(error)
      if (!usage) {
        throw error
      }
      return confirmDeleteWithUsage(usage)
    })
  }
  
  

  const providerHandlers: ProviderCardHandlers = {
    onRotateKey: handleProviderRotateKey,
    onToggleEnabled: handleProviderToggle,
    onAddEndpoint: handleProviderAddEndpoint,
    onSaveEndpoint: handleProviderSaveEndpoint,
    onDeleteEndpoint: handleProviderDeleteEndpoint,
    onClearKey: handleProviderClearKey,
    onTestEndpoint: handleProviderTestEndpoint,
  }

  return { endpointTestStatuses, providerHandlers }
}
