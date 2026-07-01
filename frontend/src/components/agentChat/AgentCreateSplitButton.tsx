import { useCallback, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Bot, Building2, ChevronDown, Loader2, Plus, Sparkles } from 'lucide-react'
import { Button, Dialog, Popover } from 'react-aria-components'

import type { OrganizationTemplate } from '../../api/organization'
import { joinClassNames } from './uiPrimitives'

export type TeamTemplateCreateMenu = {
  templates: OrganizationTemplate[]
  isLoading: boolean
  errorMessage?: string | null
  launchErrorMessage?: string | null
  canManageTemplates: boolean
  launchBusyTemplateId?: string | null
  onLaunchTemplate: (template: OrganizationTemplate) => void
  onOpenTemplates: () => void
}

type AgentCreateSplitButtonProps = {
  variant: 'sidebar' | 'drawer' | 'gallery'
  onCreateAgent: () => void
  createAgentDisabled: boolean
  createAgentButtonDisabled: boolean
  createAgentDisabledReason?: string | null
  menu: TeamTemplateCreateMenu
  className?: string
}

function templateDescription(template: OrganizationTemplate): string {
  const tagline = template.tagline?.trim()
  if (tagline) {
    return tagline
  }
  const category = template.category?.trim()
  return category || 'Team template'
}

export function AgentCreateSplitButton({
  variant,
  onCreateAgent,
  createAgentDisabled,
  createAgentButtonDisabled,
  createAgentDisabledReason = null,
  menu,
  className,
}: AgentCreateSplitButtonProps) {
  const triggerRef = useRef<HTMLDivElement | null>(null)
  const [open, setOpen] = useState(false)
  const launchBusy = Boolean(menu.launchBusyTemplateId)
  const menuCreateDisabled = Boolean(createAgentButtonDisabled || launchBusy)
  const footerLabel = menu.canManageTemplates ? 'Manage team templates' : 'View organization'
  const buttonClassName = variant === 'gallery'
    ? 'agent-gallery-create'
    : joinClassNames('chat-sidebar-create-btn', variant === 'drawer' && 'chat-sidebar-create-btn--drawer')

  const closeMenu = useCallback(() => setOpen(false), [])

  const handleBlankAgent = useCallback(() => {
    if (menuCreateDisabled) {
      return
    }
    closeMenu()
    onCreateAgent()
  }, [closeMenu, menuCreateDisabled, onCreateAgent])

  const handleOpenTemplates = useCallback(() => {
    closeMenu()
    menu.onOpenTemplates()
  }, [closeMenu, menu])

  const handleTemplateLaunch = useCallback((template: OrganizationTemplate) => {
    if (menuCreateDisabled) {
      return
    }
    if (createAgentDisabled) {
      closeMenu()
      onCreateAgent()
      return
    }
    closeMenu()
    menu.onLaunchTemplate(template)
  }, [closeMenu, createAgentDisabled, menu, menuCreateDisabled, onCreateAgent])

  const handleChevronPointerDownCapture = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!open) {
      return
    }
    event.preventDefault()
    event.stopPropagation()
    setOpen(false)
  }, [open])

  return (
    <div
      ref={triggerRef}
      className={joinClassNames('agent-create-split', buttonClassName, className)}
      data-create-variant={variant}
      data-variant={variant === 'gallery' ? 'sidebar' : variant}
      data-open={open ? 'true' : 'false'}
      data-disabled={createAgentDisabled ? 'true' : 'false'}
    >
      <button
        type="button"
        className="agent-create-split__main"
        onClick={onCreateAgent}
        disabled={createAgentButtonDisabled}
        aria-disabled={createAgentDisabled ? 'true' : undefined}
        title={createAgentDisabledReason ?? undefined}
      >
        <span className={variant === 'gallery' ? 'agent-create-split__gallery-icon' : 'chat-sidebar-create-btn-icon'}>
          <Plus className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className={joinClassNames('agent-create-split__label', variant !== 'gallery' && 'chat-sidebar-create-btn-label')}>
          New Agent
        </span>
      </button>
      <Button
        className="agent-create-split__chevron"
        aria-label="Choose a team template"
        aria-expanded={open}
        onPointerDownCapture={handleChevronPointerDownCapture}
        onPress={() => setOpen((current) => !current)}
      >
        <ChevronDown className="h-4 w-4" aria-hidden="true" />
      </Button>
      <Popover
        triggerRef={triggerRef}
        isOpen={open}
        onOpenChange={setOpen}
        shouldCloseOnInteractOutside={() => true}
        placement="bottom"
        containerPadding={0}
        isNonModal
        className="agent-create-menu-popover"
        data-create-variant={variant}
        data-variant={variant}
      >
        <Dialog className="agent-create-menu sidebar-settings__menu" aria-label="Create agent menu">
          <button
            type="button"
            className="agent-create-menu__item sidebar-settings__link"
            onClick={handleBlankAgent}
            disabled={menuCreateDisabled}
            aria-disabled={createAgentDisabled ? 'true' : undefined}
            title={createAgentDisabledReason ?? undefined}
          >
            <Plus className="agent-create-menu__item-icon sidebar-settings__link-icon" aria-hidden="true" />
            <span className="agent-create-menu__item-copy">
              <span className="agent-create-menu__item-title">Blank agent</span>
              <span className="agent-create-menu__item-description">Start from a fresh charter.</span>
            </span>
          </button>
          <div className="sidebar-settings__rule" role="separator" aria-hidden="true" />

          <div className="agent-create-menu__section-label">
            <Sparkles className="agent-create-menu__section-icon" aria-hidden="true" />
            <span>Team templates</span>
          </div>

          {menu.launchErrorMessage ? (
            <div className="agent-create-menu__state agent-create-menu__state--error">
              {menu.launchErrorMessage}
            </div>
          ) : null}

          {menu.isLoading ? (
            <div className="agent-create-menu__state">
              <Loader2 className="agent-create-menu__state-icon animate-spin" aria-hidden="true" />
              <span>Loading templates...</span>
            </div>
          ) : menu.errorMessage ? (
            <div className="agent-create-menu__state agent-create-menu__state--error">
              {menu.errorMessage}
            </div>
          ) : menu.templates.length > 0 ? (
            <div className="agent-create-menu__templates">
              {menu.templates.map((template) => {
                const launching = menu.launchBusyTemplateId === template.id
                const disabled = menuCreateDisabled || launchBusy
                return (
                  <button
                    key={template.id}
                    type="button"
                    className="agent-create-menu__item agent-create-menu__template sidebar-settings__link"
                    onClick={() => handleTemplateLaunch(template)}
                    disabled={disabled}
                    aria-disabled={createAgentDisabled ? 'true' : undefined}
                    title={createAgentDisabledReason ?? undefined}
                  >
                    {launching ? (
                      <Loader2 className="agent-create-menu__item-icon sidebar-settings__link-icon animate-spin" aria-hidden="true" />
                    ) : (
                      <Bot className="agent-create-menu__item-icon sidebar-settings__link-icon" aria-hidden="true" />
                    )}
                    <span className="agent-create-menu__item-copy">
                      <span className="agent-create-menu__item-title">{template.name}</span>
                      <span className="agent-create-menu__item-description">{templateDescription(template)}</span>
                    </span>
                  </button>
                )
              })}
            </div>
          ) : (
            <div className="agent-create-menu__empty">
              <span className="agent-create-menu__empty-title">No team templates yet</span>
              <span className="agent-create-menu__empty-copy">Create reusable starting points for this organization.</span>
            </div>
          )}

          <button
            type="button"
            className="agent-create-menu__footer sidebar-settings__link"
            onClick={handleOpenTemplates}
          >
            <Building2 className="agent-create-menu__footer-icon sidebar-settings__link-icon" aria-hidden="true" />
            <span>{footerLabel}</span>
          </button>
        </Dialog>
      </Popover>
    </div>
  )
}
