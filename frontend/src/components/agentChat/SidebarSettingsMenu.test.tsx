import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { SidebarSettingsMenu } from './SidebarSettingsMenu'

describe('SidebarSettingsMenu', () => {
  it('shows billing and task credits in proprietary mode', async () => {
    render(
      <SidebarSettingsMenu
        context={{ type: 'personal', id: '1', name: 'Personal' }}
        viewerEmail="person@example.com"
        isProprietaryMode={true}
        billingUrl="/console/billing/"
        taskCredits={{
          usedToday: 12.5,
          remaining: 87.5,
          resetOn: '2026-05-05',
        }}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open settings' }))

    expect(await screen.findByText('person@example.com')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Billing/i })).toHaveAttribute('href', '/console/billing/')
    expect(screen.getByRole('link', { name: /Billing/i })).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('link', { name: /Global Secrets/i })).toHaveAttribute('href', '/console/secrets/')
    expect(screen.getByRole('link', { name: /Global Secrets/i })).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('link', { name: /Integrations & MCP/i })).toHaveAttribute(
      'href',
      '/console/advanced/mcp-servers/',
    )
    expect(screen.getByRole('link', { name: /Integrations & MCP/i })).toHaveAttribute('target', '_blank')

    fireEvent.click(screen.getByRole('button', { name: /Task Credits Remaining/i }))

    expect(screen.getByText('Used Today')).toBeInTheDocument()
    expect(screen.getByText('12.5')).toBeInTheDocument()
    expect(screen.getByText('Remaining This Month')).toBeInTheDocument()
    expect(screen.getByText('87.5')).toBeInTheDocument()
    expect(screen.getByText('Resets On')).toBeInTheDocument()
    expect(screen.getByText('May 5, 2026')).toBeInTheDocument()
  })

  it('hides proprietary-only rows outside proprietary mode', async () => {
    render(
      <SidebarSettingsMenu
        context={{ type: 'personal', id: '1', name: 'Personal' }}
        viewerEmail="person@example.com"
        isProprietaryMode={false}
        billingUrl="/console/billing/"
        taskCredits={{
          usedToday: 12.5,
          remaining: 87.5,
          resetOn: '2026-05-05',
        }}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open settings' }))

    expect(await screen.findByText('person@example.com')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Billing/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Task Credits Remaining/i })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Global Secrets/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Integrations & MCP/i })).toBeInTheDocument()
  })

  it('uses organization name as identity in organization context', async () => {
    render(
      <SidebarSettingsMenu
        context={{ type: 'organization', id: 'org-1', name: 'Acme Ops' }}
        viewerEmail="person@example.com"
        isProprietaryMode={false}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open settings' }))

    expect(await screen.findByText('Acme Ops')).toBeInTheDocument()
    expect(screen.queryByText('person@example.com')).not.toBeInTheDocument()
  })

  it('renders the notifications toggle with permission status and handles changes', async () => {
    const handleNotificationsEnabledChange = vi.fn()

    render(
      <SidebarSettingsMenu
        context={{ type: 'personal', id: '1', name: 'Personal' }}
        viewerEmail="person@example.com"
        isProprietaryMode={true}
        notificationsEnabled={true}
        notificationStatus="needs_permission"
        onNotificationsEnabledChange={handleNotificationsEnabledChange}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open settings' }))

    const toggle = await screen.findByRole('switch', { name: /Notifications & sound/i })
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('Needs browser permission')).toBeInTheDocument()

    fireEvent.click(toggle)

    expect(handleNotificationsEnabledChange).toHaveBeenCalledWith(false)
  })
})
