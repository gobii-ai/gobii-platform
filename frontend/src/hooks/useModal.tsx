import type { ReactNode } from 'react'
import { useCallback, useState } from 'react'

type ModalRenderer = (onClose: () => void) => ReactNode

export function useModal(): [ReactNode | null, (renderer: ModalRenderer) => void, () => void] {
  const [renderer, setRenderer] = useState<ModalRenderer | null>(null)

  const close = useCallback(() => {
    setRenderer(null)
  }, [])

  const showModal = useCallback((nextRenderer: ModalRenderer) => {
    setRenderer(() => nextRenderer)
  }, [])

  const modal = renderer ? renderer(close) : null

  return [modal, showModal, close]
}
