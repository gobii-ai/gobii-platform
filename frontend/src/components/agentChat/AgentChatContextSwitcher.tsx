import { useCallback, useMemo, useState } from 'react'
import { Check, ChevronDown, UserRound, Users } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogTrigger,
  Header,
  ListBox,
  ListBoxItem,
  ListBoxSection,
  Popover,
  type Key,
  type Selection,
} from 'react-aria-components'

import type { ConsoleContext, ConsoleContextOption } from '../../api/context'

export type AgentChatContextSwitcherData = {
  current: ConsoleContext
  personal: ConsoleContext
  organizations: ConsoleContextOption[]
  onSwitch: (context: ConsoleContext) => void | Promise<void>
  isBusy?: boolean
  errorMessage?: string | null
}

type AgentChatContextSwitcherProps = AgentChatContextSwitcherData & {
  variant?: 'sidebar' | 'drawer'
  collapsed?: boolean
}

export function AgentChatContextSwitcher({
  current,
  personal,
  organizations,
  onSwitch,
  isBusy = false,
  errorMessage,
  variant = 'sidebar',
  collapsed = false,
}: AgentChatContextSwitcherProps) {
  const [open, setOpen] = useState(false)
  const personalKey = `personal:${personal.id}`
  const organizationOptions = useMemo(
    () =>
      organizations.map((org) => ({
        key: `org:${org.id}`,
        context: { type: 'organization' as const, id: org.id, name: org.name },
        role: org.role ?? null,
      })),
    [organizations],
  )
  const selectedKey = current.type === 'personal' ? personalKey : `org:${current.id}`
  const selectedKeys = useMemo(() => new Set<Key>([selectedKey]), [selectedKey])
  const contextByKey = useMemo(() => {
    const entries = new Map<string, ConsoleContext>()
    entries.set(personalKey, personal)
    organizationOptions.forEach((option) => {
      entries.set(option.key, option.context)
    })
    return entries
  }, [organizationOptions, personal, personalKey])

  const handleSelect = useCallback(
    (keys: Selection) => {
      if (isBusy) {
        return
      }
      const resolvedKey = (() => {
        if (keys === 'all') return null
        if (typeof keys === 'string' || typeof keys === 'number') {
          return String(keys)
        }
        const [first] = keys as Set<Key>
        return first ? String(first) : null
      })()
      if (!resolvedKey) {
        return
      }
      if (resolvedKey === selectedKey) {
        setOpen(false)
        return
      }
      const nextContext = contextByKey.get(resolvedKey)
      if (!nextContext) {
        return
      }
      void onSwitch(nextContext)
      setOpen(false)
    },
    [contextByKey, isBusy, onSwitch, selectedKey],
  )

  const triggerIcon = current.type === 'organization' ? (
    <Users className="chat-context-switcher__icon" aria-hidden="true" />
  ) : (
    <UserRound className="chat-context-switcher__icon" aria-hidden="true" />
  )

  return (
    <div
      className={`chat-context-switcher chat-context-switcher--${variant}`}
      data-collapsed={collapsed ? 'true' : 'false'}
    >
      <DialogTrigger isOpen={open} onOpenChange={setOpen}>
        <Button
          className="chat-context-switcher__trigger"
          aria-label={`Switch context (${current.name})`}
          data-open={open ? 'true' : 'false'}
          data-busy={isBusy ? 'true' : 'false'}
          isDisabled={isBusy}
        >
          {triggerIcon}
          <span className="chat-context-switcher__label">{current.name}</span>
          <ChevronDown className="chat-context-switcher__chevron" aria-hidden="true" />
        </Button>
        <Popover className="chat-context-switcher__popover">
          <Dialog className="chat-context-switcher__menu">
            <ListBox
              aria-label="Select workspace context"
              selectionMode="single"
              selectionBehavior="replace"
              selectedKeys={selectedKeys as unknown as Selection}
              onSelectionChange={(keys) => handleSelect(keys as Selection)}
              className="chat-context-switcher__list"
            >
              <ListBoxSection className="chat-context-switcher__section">
                <Header className="chat-context-switcher__heading">Personal</Header>
                <ListBoxItem
                  id={personalKey}
                  textValue={personal.name}
                  className="chat-context-switcher__item"
                >
                  {({ isSelected }) => (
                    <>
                      <UserRound className="chat-context-switcher__item-icon" aria-hidden="true" />
                      <span className="chat-context-switcher__item-name">{personal.name}</span>
                      {isSelected ? (
                        <Check className="chat-context-switcher__item-check" aria-hidden="true" />
                      ) : null}
                    </>
                  )}
                </ListBoxItem>
              </ListBoxSection>
              {organizationOptions.length ? (
                <ListBoxSection className="chat-context-switcher__section">
                  <Header className="chat-context-switcher__heading">Organizations</Header>
                  {organizationOptions.map((option) => (
                    <ListBoxItem
                      key={option.key}
                      id={option.key}
                      textValue={option.context.name}
                      className="chat-context-switcher__item"
                    >
                      {({ isSelected }) => (
                        <>
                          <Users className="chat-context-switcher__item-icon" aria-hidden="true" />
                          <span className="chat-context-switcher__item-name">
                            {option.context.name}
                            {option.role ? (
                              <span className="chat-context-switcher__item-role">{option.role}</span>
                            ) : null}
                          </span>
                          {isSelected ? (
                            <Check className="chat-context-switcher__item-check" aria-hidden="true" />
                          ) : null}
                        </>
                      )}
                    </ListBoxItem>
                  ))}
                </ListBoxSection>
              ) : null}
            </ListBox>
            {errorMessage ? <div className="chat-context-switcher__error">{errorMessage}</div> : null}
          </Dialog>
        </Popover>
      </DialogTrigger>
    </div>
  )
}
