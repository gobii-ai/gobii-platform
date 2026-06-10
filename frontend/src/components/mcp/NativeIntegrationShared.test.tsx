import { afterEach, describe, expect, it, vi } from 'vitest'

import type { NativeIntegrationPickerTokenResponse } from '../../api/nativeIntegrations'
import { openGoogleDrivePicker } from './NativeIntegrationShared'

describe('openGoogleDrivePicker', () => {
  afterEach(() => {
    document.querySelectorAll('script[data-google-api-script="true"]').forEach((script) => script.remove())
    delete (window as typeof window & { gapi?: unknown }).gapi
    delete (window as typeof window & { google?: unknown }).google
  })

  it('returns selected Google file metadata', async () => {
    let callback: ((data: Record<string, unknown>) => void) | null = null
    const picker = {
      Action: { PICKED: 'picked', CANCEL: 'cancel' },
      Document: {
        ID: 'id',
        NAME: 'name',
        MIME_TYPE: 'mimeType',
        URL: 'url',
      },
      Feature: { MULTISELECT_ENABLED: 'multiselect' },
      Response: { ACTION: 'action', DOCUMENTS: 'docs' },
      ViewId: { DOCS: 'docs' },
      DocsView: class {
        setMimeTypes() {
          return this
        }
      },
      PickerBuilder: class {
        addView() {
          return this
        }
        setOAuthToken() {
          return this
        }
        setDeveloperKey() {
          return this
        }
        setAppId() {
          return this
        }
        enableFeature() {
          return this
        }
        setCallback(nextCallback: (data: Record<string, unknown>) => void) {
          callback = nextCallback
          return this
        }
        build() {
          return {
            setVisible: (visible: boolean) => {
              if (visible) {
                callback?.({
                  action: 'picked',
                  docs: [
                    {
                      id: 'sheet-123',
                      name: 'Q2 Sales Tracker',
                      mimeType: 'application/vnd.google-apps.spreadsheet',
                      url: 'https://docs.google.com/spreadsheets/d/sheet-123/edit',
                    },
                  ],
                })
              }
            },
          }
        }
      },
    }
    ;(window as typeof window & { google: unknown }).google = { picker }
    ;(window as typeof window & { gapi: unknown }).gapi = {
      load: vi.fn((_apiName: string, config: { callback: () => void }) => config.callback()),
    }
    const token: NativeIntegrationPickerTokenResponse = {
      accessToken: 'access-token',
      developerKey: 'developer-key',
      appId: 'app-id',
      scope: 'https://www.googleapis.com/auth/drive.file',
      expiresAt: null,
    }

    const selectedFilesPromise = openGoogleDrivePicker(token)
    document.querySelector<HTMLScriptElement>('script[data-google-api-script="true"]')?.dispatchEvent(new Event('load'))

    await expect(selectedFilesPromise).resolves.toEqual([
      {
        externalId: 'sheet-123',
        name: 'Q2 Sales Tracker',
        mimeType: 'application/vnd.google-apps.spreadsheet',
        webUrl: 'https://docs.google.com/spreadsheets/d/sheet-123/edit',
      },
    ])
  })
})
