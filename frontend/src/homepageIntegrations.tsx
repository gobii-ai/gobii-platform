import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import './index.css'
import './styles/agentChatLegacy.css'

import { HomepageIntegrationsModal, type HomepageIntegrationsModalProps } from './components/homepage/HomepageIntegrationsModal'

const mountNode = document.getElementById('homepage-integrations-root')

if (mountNode) {
  const propsId = mountNode.dataset.propsJsonId
  if (!propsId) {
    throw new Error('Homepage integrations props script identifier is required')
  }
  const script = document.getElementById(propsId)
  if (!script || !script.textContent) {
    throw new Error(`Homepage integrations props script ${propsId} was not found`)
  }

  const props = JSON.parse(script.textContent) as HomepageIntegrationsModalProps

  const queryClient = new QueryClient()

  createRoot(mountNode).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <HomepageIntegrationsModal {...props} />
      </QueryClientProvider>
    </StrictMode>,
  )
}
