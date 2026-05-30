import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import './index.css'

import { HomepageIntegrationsModal, type HomepageIntegrationsModalProps } from './components/homepage/HomepageIntegrationsModal'

type MountOptions = {
  openOnMount?: boolean
}

let mounted = false

function readProps(mountNode: HTMLElement): HomepageIntegrationsModalProps {
  const propsId = mountNode.dataset.propsJsonId
  if (!propsId) {
    throw new Error('Homepage integrations props script identifier is required')
  }
  const script = document.getElementById(propsId)
  if (!script || !script.textContent) {
    throw new Error(`Homepage integrations props script ${propsId} was not found`)
  }

  return JSON.parse(script.textContent) as HomepageIntegrationsModalProps
}

function clearServerRenderedSelectedFields(props: HomepageIntegrationsModalProps) {
  const selectedFieldsContainer = document.getElementById(props.selectedFieldsContainerId)
  if (selectedFieldsContainer) {
    selectedFieldsContainer.textContent = ''
  }
}

export function mountHomepageIntegrations({ openOnMount = false }: MountOptions = {}) {
  if (mounted) {
    if (openOnMount) {
      document.dispatchEvent(new Event('homepage-integrations:open'))
    }
    return
  }

  const mountNode = document.getElementById('homepage-integrations-root')
  if (!mountNode) {
    return
  }

  const props = readProps(mountNode)
  clearServerRenderedSelectedFields(props)

  const queryClient = new QueryClient()
  createRoot(mountNode).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <HomepageIntegrationsModal {...props} initialOpen={openOnMount} />
      </QueryClientProvider>
    </StrictMode>,
  )
  mounted = true
}
