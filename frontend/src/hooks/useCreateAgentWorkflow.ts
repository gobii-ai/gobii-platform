import { useEffect, useMemo, useRef } from 'react'

import { chatActions, loadAgentSpawnIntent, selectCreateAgentWorkflow } from '../store/chatSlice'
import { useAppDispatch, useAppSelector } from '../store/hooks'

type UseCreateAgentWorkflowOptions = {
  appLocationSearch?: string
  isNewAgent: boolean
}

export function useCreateAgentWorkflow({
  appLocationSearch,
  isNewAgent,
}: UseCreateAgentWorkflowOptions) {
  const dispatch = useAppDispatch()
  const createAgentWorkflow = useAppSelector(selectCreateAgentWorkflow)
  const spawnIntentAutoSubmittedRef = useRef(false)
  const currentLocationSearch = appLocationSearch ?? (typeof window === 'undefined' ? '' : window.location.search)
  const spawnFlow = useMemo(() => {
    if (!isNewAgent || typeof window === 'undefined') {
      return false
    }
    const params = new URLSearchParams(currentLocationSearch)
    const flag = (params.get('spawn') || '').toLowerCase()
    return flag === '1' || flag === 'true' || flag === 'yes' || flag === 'on'
  }, [currentLocationSearch, isNewAgent])
  const spawnIntent = createAgentWorkflow.spawnIntent
  const createAgentTrialOnboarding = createAgentWorkflow.trialOnboardingTarget
  const onboardingTarget = createAgentTrialOnboarding ?? spawnIntent?.onboarding_target ?? null
  const requiresTrialPlanSelection = Boolean(
    createAgentTrialOnboarding || spawnIntent?.requires_plan_selection,
  )

  useEffect(() => {
    if (!isNewAgent) {
      spawnIntentAutoSubmittedRef.current = false
      dispatch(chatActions.spawnIntentSet(null))
      dispatch(chatActions.spawnIntentStatusSet('idle'))
      return
    }
    const request = dispatch(loadAgentSpawnIntent())
    return () => {
      request.abort()
    }
  }, [currentLocationSearch, dispatch, isNewAgent])

  return {
    createAgentError: createAgentWorkflow.error,
    createAgentTrialOnboarding,
    createAgentWorkflow,
    onboardingTarget,
    requiresTrialPlanSelection,
    spawnFlow,
    spawnIntent,
    spawnIntentAutoSubmittedRef,
    spawnIntentStatus: createAgentWorkflow.spawnIntentStatus,
  }
}
