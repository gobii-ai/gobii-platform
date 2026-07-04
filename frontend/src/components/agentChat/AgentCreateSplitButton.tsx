import { useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Bot, Building2, ChevronDown, Loader2, Plus } from 'lucide-react'
import { Button, Dialog, Popover } from 'react-aria-components'

import type { OrganizationTemplate } from '../../api/organization'
import { joinClassNames } from './uiPrimitives'

export type TeamTemplateCreateMenu = { templates: OrganizationTemplate[]; isLoading: boolean; errorMessage?: string | null; launchErrorMessage?: string | null; canManageTemplates: boolean; launchBusyTemplateId?: string | null; onLaunchTemplate: (template: OrganizationTemplate) => void; onOpenTemplates: () => void }

type AgentCreateSplitButtonProps = { variant: 'sidebar' | 'drawer' | 'gallery'; onCreateAgent: () => void; createAgentDisabled: boolean; createAgentButtonDisabled: boolean; createAgentDisabledReason?: string | null; menu: TeamTemplateCreateMenu; className?: string }

function templateDescription(template: OrganizationTemplate): string {
  return template.tagline?.trim() || template.category?.trim() || 'Team template'
}

type CreateMenuItemProps = ButtonHTMLAttributes<HTMLButtonElement> & { icon: ReactNode; label: ReactNode; description?: ReactNode }

function CreateMenuItem({ icon, label, description, ...buttonProps }: CreateMenuItemProps) {
  return (
    <button type="button" className="sidebar-settings__link" {...buttonProps}>
      {icon}
      <span className="sidebar-settings__notification-copy">
        <span className="sidebar-settings__notification-title">{label}</span>
        {description ? <span className="sidebar-settings__notification-status">{description}</span> : null}
      </span>
    </button>
  )
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
  const disabledMenuItemProps: ButtonHTMLAttributes<HTMLButtonElement> = { disabled: menuCreateDisabled, 'aria-disabled': createAgentDisabled ? true : undefined, title: createAgentDisabledReason ?? undefined }

  const closeMenu = () => setOpen(false)
  const handleBlankAgent = () => {
    if (!menuCreateDisabled) {
      closeMenu()
      onCreateAgent()
    }
  }
  const handleOpenTemplates = () => { closeMenu(); menu.onOpenTemplates() }
  const handleTemplateLaunch = (template: OrganizationTemplate) => {
    if (menuCreateDisabled) return
    if (createAgentDisabled) {
      closeMenu()
      onCreateAgent()
      return
    }
    closeMenu()
    menu.onLaunchTemplate(template)
  }

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
        <span className={variant === 'gallery' ? undefined : 'chat-sidebar-create-btn-icon'}>
          <Plus className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className={variant !== 'gallery' ? 'chat-sidebar-create-btn-label' : undefined}>
          New Agent
        </span>
      </button>
      <Button
        className="agent-create-split__chevron"
        aria-label="Choose a team template"
        aria-expanded={open}
        onPointerDownCapture={(event) => {
          if (open) {
            event.preventDefault()
            event.stopPropagation()
            setOpen(false)
          }
        }}
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
        className="sidebar-settings__popover"
        data-variant={variant === 'sidebar' ? 'sidebar' : 'drawer'}
        data-collapsed="false"
      >
        <Dialog className="agent-create-menu sidebar-settings__menu" aria-label="Create agent menu">
          <CreateMenuItem
            icon={<Plus className="sidebar-settings__link-icon" aria-hidden="true" />}
            label="Blank agent"
            description="Start from a fresh charter."
            onClick={handleBlankAgent}
            {...disabledMenuItemProps}
          />
          <div className="sidebar-settings__rule" role="separator" aria-hidden="true" />

          <div className="sidebar-settings__identity">
            <span className="sidebar-settings__identity-label">Team templates</span>
          </div>

          {menu.launchErrorMessage ? (
            <div className="agent-create-menu__state agent-create-menu__state--error">
              {menu.launchErrorMessage}
            </div>
          ) : null}

          {menu.isLoading ? (
            <div className="agent-create-menu__state">
              <Loader2 className="sidebar-settings__link-icon animate-spin" aria-hidden="true" />
              <span>Loading templates...</span>
            </div>
          ) : menu.errorMessage ? (
            <div className="agent-create-menu__state agent-create-menu__state--error">
              {menu.errorMessage}
            </div>
          ) : menu.templates.length > 0 ? (
            <div className="agent-create-menu__templates sidebar-settings__links">
              {menu.templates.map((template) => {
                const launching = menu.launchBusyTemplateId === template.id
                return (
                  <CreateMenuItem
                    key={template.id}
                    icon={launching
                      ? <Loader2 className="sidebar-settings__link-icon animate-spin" aria-hidden="true" />
                      : <Bot className="sidebar-settings__link-icon" aria-hidden="true" />}
                    label={template.name}
                    description={templateDescription(template)}
                    onClick={() => handleTemplateLaunch(template)}
                    {...disabledMenuItemProps}
                  />
                )
              })}
            </div>
          ) : (
            <div className="agent-create-menu__empty">
              <span className="sidebar-settings__notification-title">No team templates yet</span>
              <span className="sidebar-settings__notification-status">Create reusable starting points for this organization.</span>
            </div>
          )}

          <CreateMenuItem
            icon={<Building2 className="sidebar-settings__link-icon" aria-hidden="true" />}
            label={footerLabel}
            onClick={handleOpenTemplates}
          />
        </Dialog>
      </Popover>
    </div>
  )
}
