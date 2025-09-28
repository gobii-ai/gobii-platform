import 'vite/modulepreload-polyfill'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

const mountNode = document.getElementById('gobii-frontend-root')

if (!mountNode) {
  throw new Error('Gobii frontend mount element not found')
}

const agentId = mountNode.dataset.agentId || null
const agentName = mountNode.dataset.agentName || null

createRoot(mountNode).render(
  <StrictMode>
    <App agentId={agentId} agentName={agentName} />
  </StrictMode>,
)
