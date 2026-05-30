type HomepageIntegrationsModalModule = typeof import('./homepageIntegrationsModal')

const mountNode = document.getElementById('homepage-integrations-root')

if (mountNode) {
  let modalModulePromise: Promise<HomepageIntegrationsModalModule> | null = null

  const loadModalModule = () => {
    if (!modalModulePromise) {
      modalModulePromise = import('./homepageIntegrationsModal')
    }
    return modalModulePromise
  }

  const parseInitialSearchTerm = () => {
    const propsId = mountNode.dataset.propsJsonId
    if (!propsId) {
      return ''
    }
    const script = document.getElementById(propsId)
    if (!script?.textContent) {
      return ''
    }
    try {
      const props = JSON.parse(script.textContent) as { initialSearchTerm?: unknown }
      return typeof props.initialSearchTerm === 'string' ? props.initialSearchTerm.trim() : ''
    } catch (error) {
      console.error('Failed to parse homepage integrations props.', error)
      return ''
    }
  }

  const setLoadingState = (loading: boolean) => {
    document.querySelectorAll<HTMLElement>('[data-integrations-open]').forEach((button) => {
      if (loading) {
        button.setAttribute('aria-busy', 'true')
      } else {
        button.removeAttribute('aria-busy')
      }
    })
  }

  const preloadModal = () => {
    loadModalModule().catch((error) => {
      console.error('Failed to load homepage integrations.', error)
      modalModulePromise = null
    })
  }

  const openModal = async (event?: Event) => {
    event?.preventDefault()
    setLoadingState(true)
    try {
      const module = await loadModalModule()
      module.mountHomepageIntegrations({ openOnMount: true })
    } catch (error) {
      console.error('Failed to open homepage integrations.', error)
      modalModulePromise = null
    } finally {
      setLoadingState(false)
    }
  }

  document.querySelectorAll<HTMLElement>('[data-integrations-open]').forEach((button) => {
    button.addEventListener('click', openModal)
    button.addEventListener('pointerdown', preloadModal, { passive: true })
    button.addEventListener('pointerenter', preloadModal, { passive: true })
    button.addEventListener('focus', preloadModal)
  })

  if (parseInitialSearchTerm()) {
    void openModal()
  }
}
