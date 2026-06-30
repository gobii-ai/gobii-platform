import { useCallback, useMemo, useState } from 'react'
import { BarChart3, Building2, Check, ChevronDown, CreditCard, KeyRound, LayoutGrid, ServerCog, UserRound, type LucideIcon } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogTrigger,
  ListBox,
  ListBoxItem,
  Popover,
  type Key,
  type Selection,
} from 'react-aria-components'

export type SelectionShellPage = 'agents' | 'billing' | 'profile' | 'organization' | 'secrets' | 'usage' | 'integrations' | 'api-keys'

export const SELECTION_SHELL_PAGE_LABELS: Record<SelectionShellPage, string> = {
  agents: 'My Agents',
  billing: 'Billing',
  profile: 'Profile',
  organization: 'Team',
  secrets: 'Secrets',
  usage: 'Usage',
  integrations: 'Integrations',
  'api-keys': 'API Keys',
}

type SelectionPageOption = {
  key: SelectionShellPage
  label: string
  icon: LucideIcon
}

const PAGE_OPTIONS: SelectionPageOption[] = [
  { key: 'agents', label: SELECTION_SHELL_PAGE_LABELS.agents, icon: LayoutGrid },
  { key: 'billing', label: SELECTION_SHELL_PAGE_LABELS.billing, icon: CreditCard },
  { key: 'profile', label: SELECTION_SHELL_PAGE_LABELS.profile, icon: UserRound },
  { key: 'organization', label: SELECTION_SHELL_PAGE_LABELS.organization, icon: Building2 },
  { key: 'secrets', label: SELECTION_SHELL_PAGE_LABELS.secrets, icon: KeyRound },
  { key: 'usage', label: SELECTION_SHELL_PAGE_LABELS.usage, icon: BarChart3 },
  { key: 'integrations', label: SELECTION_SHELL_PAGE_LABELS.integrations, icon: ServerCog },
  { key: 'api-keys', label: SELECTION_SHELL_PAGE_LABELS['api-keys'], icon: KeyRound },
]

type SelectionShellPageSwitcherProps = {
  currentPage: SelectionShellPage
  onSelectPage: (page: SelectionShellPage) => void
  showOrganization?: boolean
}

export function SelectionShellPageSwitcher({
  currentPage,
  onSelectPage,
  showOrganization = true,
}: SelectionShellPageSwitcherProps) {
  const [open, setOpen] = useState(false)
  const selectedKeys = useMemo(() => new Set<Key>([currentPage]), [currentPage])
  const pageOptions = useMemo(
    () => PAGE_OPTIONS.filter((option) => showOrganization || option.key !== 'organization'),
    [showOrganization],
  )

  const handleSelectionChange = useCallback(
    (keys: Selection) => {
      const resolvedKey = (() => {
        if (keys === 'all') {
          return null
        }
        if (typeof keys === 'string' || typeof keys === 'number') {
          return String(keys)
        }
        const [first] = keys as Set<Key>
        return first ? String(first) : null
      })()
      if (!resolvedKey) {
        return
      }
      const nextPage = pageOptions.find((option) => option.key === resolvedKey)?.key
      if (!nextPage) {
        return
      }
      setOpen(false)
      if (nextPage !== currentPage) {
        onSelectPage(nextPage)
      }
    },
    [currentPage, onSelectPage, pageOptions],
  )

  return (
    <DialogTrigger isOpen={open} onOpenChange={setOpen}>
      <Button
        className="selection-shell-switcher__trigger"
        aria-label={`Switch page (${SELECTION_SHELL_PAGE_LABELS[currentPage]})`}
        data-open={open ? 'true' : 'false'}
      >
        <span className="selection-shell-switcher__label">{SELECTION_SHELL_PAGE_LABELS[currentPage]}</span>
        <ChevronDown
          className="selection-shell-switcher__chevron"
          aria-hidden="true"
        />
      </Button>
      <Popover className="selection-shell-switcher__popover sidebar-settings__popover">
        <Dialog className="selection-shell-switcher__menu sidebar-settings__menu">
          <ListBox
            aria-label="Switch shell page"
            selectionMode="single"
            selectionBehavior="replace"
            selectedKeys={selectedKeys as unknown as Selection}
            onSelectionChange={(keys) => handleSelectionChange(keys as Selection)}
            className="selection-shell-switcher__list"
          >
            {pageOptions.map((option) => {
              const Icon = option.icon
              return (
                <ListBoxItem
                  key={option.key}
                  id={option.key}
                  textValue={option.label}
                  className="selection-shell-switcher__item sidebar-settings__link"
                >
                  {({ isSelected }) => (
                    <>
                      <Icon className="selection-shell-switcher__item-icon sidebar-settings__link-icon" aria-hidden="true" />
                      <span className="selection-shell-switcher__item-label">{option.label}</span>
                      {isSelected ? <Check className="selection-shell-switcher__item-check" aria-hidden="true" /> : null}
                    </>
                  )}
                </ListBoxItem>
              )
            })}
          </ListBox>
        </Dialog>
      </Popover>
    </DialogTrigger>
  )
}
