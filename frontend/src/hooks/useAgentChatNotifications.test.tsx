import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useEffect } from 'react'

import type { AgentMessageNotification } from '../types/agentChat'
import {
  useAgentChatNotifications,
  shouldDispatchAgentChatNotification,
} from './useAgentChatNotifications'

class FakeNotification {
  static permission: NotificationPermission = 'granted'
  static nextPermission: NotificationPermission = 'granted'
  static instances: FakeNotification[] = []
  static requestPermissionMock = vi.fn(async () => {
    FakeNotification.permission = FakeNotification.nextPermission
    return FakeNotification.permission
  })

  static requestPermission() {
    return FakeNotification.requestPermissionMock()
  }

  title: string
  options: NotificationOptions
  onclick: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(title: string, options: NotificationOptions = {}) {
    this.title = title
    this.options = options
    FakeNotification.instances.push(this)
  }

  close() {
    this.onclose?.()
  }
}

class FakeGainNode {
  gain = {
    setValueAtTime: vi.fn(),
    exponentialRampToValueAtTime: vi.fn(),
  }

  connect = vi.fn()
}

class FakeOscillatorNode {
  type = 'sine'
  frequency = {
    setValueAtTime: vi.fn(),
    exponentialRampToValueAtTime: vi.fn(),
  }

  connect = vi.fn()
  start = vi.fn(() => {
    FakeAudioContext.startCount += 1
  })
  stop = vi.fn()
}

class FakeAudioContext {
  static instances: FakeAudioContext[] = []
  static startCount = 0

  state: AudioContextState = 'suspended'
  currentTime = 0
  destination = {} as AudioDestinationNode

  constructor() {
    FakeAudioContext.instances.push(this)
  }

  resume = vi.fn(async () => {
    this.state = 'running'
  })

  createGain() {
    return new FakeGainNode() as unknown as GainNode
  }

  createOscillator() {
    return new FakeOscillatorNode() as unknown as OscillatorNode
  }
}

type HarnessProps = {
  enabled?: boolean
  activeAgentId?: string | null
  currentContext?: { type: 'personal' | 'organization'; id: string; name: string } | null
  onOpenAgent?: (agentId: string) => void
}

let latestHookState: ReturnType<typeof useAgentChatNotifications> | null = null

function HookHarness({
  enabled = true,
  activeAgentId = 'agent-1',
  currentContext = { type: 'personal', id: 'user-1', name: 'Test User' },
  onOpenAgent = () => undefined,
}: HarnessProps) {
  const hookState = useAgentChatNotifications({
    enabled,
    currentContext,
    activeAgentId,
    onOpenAgent,
  })

  useEffect(() => {
    latestHookState = hookState
  }, [hookState])

  return null
}

function buildNotificationEvent(overrides: Partial<AgentMessageNotification> = {}): AgentMessageNotification {
  return {
    agent_id: 'agent-1',
    agent_name: 'Agent One',
    agent_avatar_url: 'https://example.com/avatar.png',
    workspace: {
      type: 'personal',
      id: 'user-1',
    },
    message: {
      id: 'message-1',
      body_preview: 'Finished the task.',
      timestamp: '2026-04-28T12:00:00Z',
      channel: 'web',
    },
    ...overrides,
  }
}

function setPageFocus({ visible, focused }: { visible: boolean; focused: boolean }) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => (visible ? 'visible' : 'hidden'),
  })
  Object.defineProperty(document, 'hasFocus', {
    configurable: true,
    value: () => focused,
  })
}

describe('useAgentChatNotifications', () => {
  beforeEach(() => {
    cleanup()
    latestHookState = null
    FakeNotification.permission = 'granted'
    FakeNotification.nextPermission = 'granted'
    FakeNotification.instances = []
    FakeNotification.requestPermissionMock.mockClear()
    FakeAudioContext.instances = []
    FakeAudioContext.startCount = 0
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      value: FakeNotification,
    })
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      value: FakeAudioContext,
    })
    Object.defineProperty(window, 'focus', {
      configurable: true,
      value: vi.fn(),
    })
    setPageFocus({ visible: true, focused: true })
  })

  afterEach(() => {
    cleanup()
  })

  it('suppresses notifications for the active agent while the page is focused', async () => {
    render(<HookHarness />)
    window.dispatchEvent(new Event('pointerdown'))
    await waitFor(() => {
      expect(FakeAudioContext.instances).toHaveLength(1)
    })

    latestHookState?.handleMessageNotificationEvent(buildNotificationEvent())

    expect(FakeNotification.instances).toHaveLength(0)
    expect(FakeAudioContext.startCount).toBe(0)
  })

  it('notifies for the active agent when the page is backgrounded', async () => {
    setPageFocus({ visible: false, focused: false })
    render(<HookHarness />)
    window.dispatchEvent(new Event('pointerdown'))
    await waitFor(() => {
      expect(FakeAudioContext.instances).toHaveLength(1)
    })

    latestHookState?.handleMessageNotificationEvent(buildNotificationEvent())

    await waitFor(() => {
      expect(FakeNotification.instances).toHaveLength(1)
      expect(FakeAudioContext.startCount).toBeGreaterThan(0)
    })
    expect(FakeNotification.instances[0].title).toBe('New message from Agent One')
  })

  it('notifies for a different agent while the page is focused', async () => {
    render(<HookHarness />)
    window.dispatchEvent(new Event('pointerdown'))
    await waitFor(() => {
      expect(FakeAudioContext.instances).toHaveLength(1)
    })

    latestHookState?.handleMessageNotificationEvent(
      buildNotificationEvent({
        agent_id: 'agent-2',
        agent_name: 'Agent Two',
        message: {
          id: 'message-2',
          body_preview: 'New update.',
          timestamp: '2026-04-28T12:01:00Z',
          channel: 'web',
        },
      }),
    )

    await waitFor(() => {
      expect(FakeNotification.instances).toHaveLength(1)
      expect(FakeAudioContext.startCount).toBeGreaterThan(0)
    })
  })

  it('ignores notifications from a different workspace', () => {
    render(<HookHarness currentContext={{ type: 'organization', id: 'org-1', name: 'Acme' }} />)

    latestHookState?.handleMessageNotificationEvent(buildNotificationEvent())

    expect(FakeNotification.instances).toHaveLength(0)
  })

  it('dedupes repeated notifications for the same message id', async () => {
    setPageFocus({ visible: false, focused: false })
    render(<HookHarness />)
    window.dispatchEvent(new Event('pointerdown'))
    await waitFor(() => {
      expect(FakeAudioContext.instances).toHaveLength(1)
    })

    const event = buildNotificationEvent()
    latestHookState?.handleMessageNotificationEvent(event)
    latestHookState?.handleMessageNotificationEvent(event)

    await waitFor(() => {
      expect(FakeNotification.instances).toHaveLength(1)
    })
  })

  it('handles default and denied notification permissions while still reporting status changes', async () => {
    FakeNotification.permission = 'default'
    render(<HookHarness />)
    window.dispatchEvent(new Event('pointerdown'))
    await waitFor(() => {
      expect(FakeAudioContext.instances).toHaveLength(1)
    })

    expect(latestHookState?.notificationStatus).toBe('needs_permission')

    FakeNotification.nextPermission = 'denied'
    await latestHookState?.requestNotificationPermission()

    await waitFor(() => {
      expect(latestHookState?.notificationStatus).toBe('blocked')
    })

    setPageFocus({ visible: false, focused: false })
    latestHookState?.handleMessageNotificationEvent(buildNotificationEvent())

    await waitFor(() => {
      expect(FakeNotification.instances).toHaveLength(0)
      expect(FakeAudioContext.startCount).toBeGreaterThan(0)
    })
  })

  it('focuses the window and opens the target agent when the notification is clicked', async () => {
    const onOpenAgent = vi.fn()
    setPageFocus({ visible: false, focused: false })
    render(<HookHarness onOpenAgent={onOpenAgent} />)

    latestHookState?.handleMessageNotificationEvent(
      buildNotificationEvent({
        agent_id: 'agent-2',
        agent_name: 'Agent Two',
        message: {
          id: 'message-2',
          body_preview: 'Please review.',
          timestamp: '2026-04-28T12:02:00Z',
          channel: 'web',
        },
      }),
    )

    const notification = FakeNotification.instances[0]
    expect(notification).toBeTruthy()

    notification.onclick?.()

    expect(window.focus).toHaveBeenCalled()
    expect(onOpenAgent).toHaveBeenCalledWith('agent-2')
  })

  it('exposes the workspace filtering rule used for notification dispatch', () => {
    setPageFocus({ visible: true, focused: true })

    expect(shouldDispatchAgentChatNotification({
      event: buildNotificationEvent({
        agent_id: 'agent-2',
      }),
      currentContext: { type: 'personal', id: 'user-1', name: 'Test User' },
      activeAgentId: 'agent-1',
    })).toBe(true)

    expect(shouldDispatchAgentChatNotification({
      event: buildNotificationEvent({
        workspace: { type: 'organization', id: 'org-1' },
      }),
      currentContext: { type: 'personal', id: 'user-1', name: 'Test User' },
      activeAgentId: 'agent-1',
    })).toBe(false)
  })
})
