import { useCallback, useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { saveNativeIntegrationCredentials, type NativeIntegrationManualConnectResponse, type NativeIntegrationProvider } from '../../api/nativeIntegrations'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { NativeIntegrationCredentialFormModal } from './NativeIntegrationCredentialFormModal'

type UseManualNativeIntegrationConnectOptions = {
  nativeQueryKey: readonly unknown[]
  getCsrfToken?: () => Promise<string>
  extraInvalidateQueryKeys?: Array<readonly unknown[]>
  onMutate?: (provider: NativeIntegrationProvider) => void
  onSuccess?: (payload: NativeIntegrationManualConnectResponse, provider: NativeIntegrationProvider) => void
  onError?: (message: string) => void
  onSettled?: () => void
}

type ManualNativeIntegrationConnectState = {
  credentialModal: ReactNode
  isPending: boolean
  openCredentialModal: (provider: NativeIntegrationProvider) => void
}

export function useManualNativeIntegrationConnect({
  nativeQueryKey,
  getCsrfToken,
  extraInvalidateQueryKeys = [],
  onMutate,
  onSuccess,
  onError,
  onSettled,
}: UseManualNativeIntegrationConnectOptions): ManualNativeIntegrationConnectState {
  const queryClient = useQueryClient()
  const [credentialProvider, setCredentialProvider] = useState<NativeIntegrationProvider | null>(null)

  const mutation = useMutation({
    mutationFn: async ({
      provider,
      credentials,
    }: {
      provider: NativeIntegrationProvider
      credentials: Record<string, string | null>
    }) => {
      const csrfToken = getCsrfToken ? await getCsrfToken() : undefined
      return saveNativeIntegrationCredentials(provider.connectUrl, credentials, csrfToken)
    },
    onMutate: ({ provider }) => {
      onMutate?.(provider)
    },
    onSuccess: (payload, { provider }) => {
      onSuccess?.(payload, provider)
      void queryClient.invalidateQueries({ queryKey: nativeQueryKey })
      for (const queryKey of extraInvalidateQueryKeys) {
        void queryClient.invalidateQueries({ queryKey, exact: false })
      }
    },
    onError: (error) => {
      onError?.(safeErrorMessage(error))
    },
    onSettled: () => {
      onSettled?.()
    },
  })

  const openCredentialModal = useCallback((provider: NativeIntegrationProvider) => {
    setCredentialProvider(provider)
  }, [])

  const credentialModal = credentialProvider ? (
    <NativeIntegrationCredentialFormModal
      provider={credentialProvider}
      onClose={() => setCredentialProvider(null)}
      onSubmit={(credentials) => mutation.mutateAsync({ provider: credentialProvider, credentials })}
    />
  ) : null

  return {
    credentialModal,
    isPending: mutation.isPending,
    openCredentialModal,
  }
}
