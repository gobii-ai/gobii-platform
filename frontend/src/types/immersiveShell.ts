export type AgentChatShellSubview = 'chat' | 'settings' | 'secrets' | 'secret-requests' | 'email' | 'files' | 'contact-requests'

export type ImmersiveEmbeddedPanel = Exclude<AgentChatShellSubview, 'chat'> | null

export type AgentChatSidebarMode = 'collapsed' | 'list' | 'gallery'

export type AgentDrawerViewMode = 'list' | 'gallery'

export type SelectionShellPage = 'agents' | 'billing' | 'profile' | 'organization' | 'secrets' | 'usage' | 'integrations' | 'api-keys'
