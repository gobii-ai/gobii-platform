import { memo, useState, useCallback, useEffect } from 'react'
import { MessageSquare, Search, Settings, ChevronLeft, ChevronRight } from 'lucide-react'

type SidebarItem = {
  id: string
  icon: typeof MessageSquare
  label: string
  active?: boolean
  onClick?: () => void
}

type ChatSidebarProps = {
  items?: SidebarItem[]
  defaultCollapsed?: boolean
  onToggle?: (collapsed: boolean) => void
}

const defaultItems: SidebarItem[] = [
  { id: 'chat', icon: MessageSquare, label: 'Chat', active: true },
  { id: 'search', icon: Search, label: 'Search' },
  { id: 'settings', icon: Settings, label: 'Settings' },
]

export const ChatSidebar = memo(function ChatSidebar({
  items = defaultItems,
  defaultCollapsed = true,
  onToggle,
}: ChatSidebarProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)
  const [isMobile, setIsMobile] = useState(false)

  // Detect mobile breakpoint
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  // On mobile, sidebar is hidden by default
  const handleToggle = useCallback(() => {
    const next = !collapsed
    setCollapsed(next)
    onToggle?.(next)
  }, [collapsed, onToggle])

  // Don't render sidebar on mobile (for now - can add drawer later)
  if (isMobile) {
    return null
  }

  return (
    <aside
      className={`chat-sidebar ${collapsed ? 'chat-sidebar--collapsed' : ''}`}
      data-collapsed={collapsed}
    >
      <div className="chat-sidebar-inner">
        {/* Toggle button */}
        <button
          type="button"
          className="chat-sidebar-toggle"
          onClick={handleToggle}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <ChevronLeft className="h-4 w-4" />
          )}
        </button>

        {/* Navigation items */}
        <nav className="chat-sidebar-nav">
          {items.map((item) => {
            const Icon = item.icon
            return (
              <button
                key={item.id}
                type="button"
                className={`chat-sidebar-item ${item.active ? 'chat-sidebar-item--active' : ''}`}
                onClick={item.onClick}
                title={collapsed ? item.label : undefined}
              >
                <span className="chat-sidebar-item-icon">
                  <Icon className="h-5 w-5" />
                </span>
                {!collapsed && (
                  <span className="chat-sidebar-item-label">{item.label}</span>
                )}
              </button>
            )
          })}
        </nav>
      </div>
    </aside>
  )
})
