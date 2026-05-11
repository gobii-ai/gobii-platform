import type { ReactNode } from 'react'
import { useCallback } from 'react'

import { useModal } from '../../hooks/useModal'
import { AgentPipedreamAppsModal } from '../mcp/AgentPipedreamAppsModal'

type ComposerPipedreamAppsControlRenderProps = {
  openModal: () => void
  disabled: boolean
  loading: boolean
}

type ComposerPipedreamAppsControlProps = {
  agentId: string
  disabled?: boolean
  children: (props: ComposerPipedreamAppsControlRenderProps) => ReactNode
}

export function ComposerPipedreamAppsControl({
  agentId,
  disabled = false,
  children,
}: ComposerPipedreamAppsControlProps) {
  const [modal, showModal] = useModal()

  const openModal = useCallback(() => {
    if (disabled) {
      return
    }
    showModal((onClose) => (
      <AgentPipedreamAppsModal agentId={agentId} onClose={onClose} />
    ))
  }, [agentId, disabled, showModal])

  const triggerDisabled = disabled

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
