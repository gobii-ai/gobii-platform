import type { ReactNode } from 'react'
import { useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'

import { fetchPipedreamAppSettings } from '../../api/mcp'
import { useModal } from '../../hooks/useModal'
import { PipedreamAppsModal } from '../mcp/PipedreamAppsModal'

type ComposerPipedreamAppsControlRenderProps = {
  openModal: () => void
  disabled: boolean
  loading: boolean
}

type ComposerPipedreamAppsControlProps = {
  settingsUrl: string
  searchUrl: string
  disabled?: boolean
  children: (props: ComposerPipedreamAppsControlRenderProps) => ReactNode
}

export function ComposerPipedreamAppsControl({
  settingsUrl,
  searchUrl,
  disabled = false,
  children,
}: ComposerPipedreamAppsControlProps) {
  const [modal, showModal] = useModal()
  const settingsQuery = useQuery({
    queryKey: ['pipedream-app-settings', settingsUrl],
    queryFn: () => fetchPipedreamAppSettings(settingsUrl),
  })

  const openModal = useCallback(() => {
    if (!settingsQuery.data || disabled) {
      return
    }
    showModal((onClose) => (
      <PipedreamAppsModal
        settingsUrl={settingsUrl}
        searchUrl={searchUrl}
        initialSettings={settingsQuery.data}
        onClose={onClose}
        onSuccess={() => {}}
        onError={() => {}}
      />
    ))
  }, [disabled, searchUrl, settingsQuery.data, settingsUrl, showModal])

  const triggerDisabled = disabled || settingsQuery.isLoading || !settingsQuery.data

  return (
    <>
      {children({
        openModal,
        disabled: triggerDisabled,
        loading: settingsQuery.isLoading,
      })}
      {modal}
    </>
  )
}
