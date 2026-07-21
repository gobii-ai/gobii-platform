import { useEffect, useRef, type Dispatch, type SetStateAction } from 'react'

import { getInitialAgentChatSidebarMode } from '../components/agentChat/sidebarMode'
import { IMMERSIVE_SIDEBAR_MODE_STORAGE_KEY, immersiveShellActions, selectImmersiveShellSubview, selectImmersiveSidebarMode } from '../store/immersiveShellSlice'
import { useAppDispatch, useAppSelector } from '../store/hooks'
import type { AgentChatSidebarMode, SelectionShellPage } from '../types/immersiveShell'
import { extractAgentChatShellAgentId, getAgentChatShellSubview } from '../util/agentChatShellRoutes'

type UseImmersiveShellBridgeOptions = {
  agentId?: string | null
  selectionPage: SelectionShellPage
  resetManualContextForExternalAgent: (agentId: string | null) => void
  setShellPathname: Dispatch<SetStateAction<string>>
  setSwitchingAgentId: (agentId: string | null) => void
  shellPathname: string
}

function readSelectionSidebarModePreference(): AgentChatSidebarMode | null {
  if (typeof window === 'undefined') {
    return null
  }
  try {
    const stored = window.sessionStorage.getItem(IMMERSIVE_SIDEBAR_MODE_STORAGE_KEY)
    if (stored === 'collapsed' || stored === 'list' || stored === 'gallery') {
      return stored
    }
  } catch {
    return null
  }
  return null
}

export function useImmersiveShellBridge({
  agentId,
  selectionPage,
  resetManualContextForExternalAgent,
  setShellPathname,
  setSwitchingAgentId,
  shellPathname,
}: UseImmersiveShellBridgeOptions) {
  const dispatch = useAppDispatch()
  const sidebarModeHydratedRef = useRef(false)
  const selectionSidebarMode = useAppSelector(selectImmersiveSidebarMode)
  const shellSubview = useAppSelector(selectImmersiveShellSubview)

  useEffect(() => {
    resetManualContextForExternalAgent(agentId ?? null)
    setShellPathname(typeof window === 'undefined' ? '' : window.location.pathname)
  }, [agentId, resetManualContextForExternalAgent, setShellPathname])

  useEffect(() => {
    dispatch(immersiveShellActions.setShellPathname(shellPathname))
    dispatch(immersiveShellActions.setShellSubview(getAgentChatShellSubview(shellPathname)))
  }, [dispatch, shellPathname])

  useEffect(() => {
    if (!sidebarModeHydratedRef.current) {
      sidebarModeHydratedRef.current = true
      dispatch(immersiveShellActions.setSidebarMode(
        agentId === undefined
          ? (selectionPage === 'agents' ? (readSelectionSidebarModePreference() ?? 'gallery') : 'gallery')
          : getInitialAgentChatSidebarMode(),
      ))
      return
    }
    if (agentId !== undefined) {
      return
    }
    if (selectionPage !== 'agents') {
      if (selectionSidebarMode !== 'gallery') {
        dispatch(immersiveShellActions.setSidebarMode('gallery'))
      }
      return
    }
    const storedSelectionMode = readSelectionSidebarModePreference()
    if (storedSelectionMode && storedSelectionMode !== selectionSidebarMode) {
      dispatch(immersiveShellActions.setSidebarMode(storedSelectionMode))
    }
  }, [agentId, dispatch, selectionPage, selectionSidebarMode])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const handleShellLocationChange = () => {
      const nextPathname = window.location.pathname
      setShellPathname(nextPathname)
      const nextAgentId = extractAgentChatShellAgentId(nextPathname)
      resetManualContextForExternalAgent(nextAgentId)
      setSwitchingAgentId(null)
    }

    window.addEventListener('popstate', handleShellLocationChange)
    return () => window.removeEventListener('popstate', handleShellLocationChange)
  }, [resetManualContextForExternalAgent, setShellPathname, setSwitchingAgentId])

  return {
    selectionSidebarMode,
    shellSubview,
  }
}
