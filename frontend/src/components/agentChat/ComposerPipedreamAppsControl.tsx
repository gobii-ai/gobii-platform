import type { ReactNode } from 'react'
import { useCallback, useEffect } from 'react'

import { useModal } from '../../hooks/useModal'
import { AgentPipedreamAppsModal } from '../mcp/AgentPipedreamAppsModal'

type ComposerPipedreamAppsControlRenderProps = {
  openModal: () => void
  disabled: boolean
  loading: boolean
}

type ComposerPipedreamAppsControlProps = {
  agentId: string
  enablePipedreamApps?: boolean
  nativeIntegrationsUrl?: string | null
  disabled?: boolean
  openRequestKey?: number
  children: (props: ComposerPipedreamAppsControlRenderProps) => ReactNode
}

export function ComposerPipedreamAppsControl({
  agentId,
  enablePipedreamApps = true,
  nativeIntegrationsUrl = null,
  disabled = false,
  openRequestKey = 0,
  children,
}: ComposerPipedreamAppsControlProps) {
  const [modal, showModal] = useModal()

  const openModal = useCallback(() => {
    if (disabled) {
      return
    }
    showModal((onClose) => (
      <AgentPipedreamAppsModal
        agentId={agentId}
        enablePipedreamApps={enablePipedreamApps}
        nativeIntegrationsUrl={nativeIntegrationsUrl}
        onClose={onClose}
      />
    ))
  }, [agentId, enablePipedreamApps, nativeIntegrationsUrl, disabled, showModal])

  const triggerDisabled = disabled

  useEffect(() => {
    if (openRequestKey > 0) {
      openModal()
    }
  }, [openModal, openRequestKey])

  return (
    <>
      {children({
        openModal,
        disabled: triggerDisabled,
        loading: false,
      })}
      {modal}
    </>
  )
}
