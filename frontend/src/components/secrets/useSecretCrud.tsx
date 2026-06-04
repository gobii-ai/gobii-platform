import { useCallback, useState } from 'react'
import { useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query'

import type { CreateSecretPayload, SecretDTO, SecretMutationResponse, UpdateSecretPayload } from '../../api/secrets'
import { DeleteSecretDialog } from './DeleteSecretDialog'
import { SecretFormModal } from './SecretFormModal'
import { useModal } from '../../hooks/useModal'

type UseSecretCrudOptions<TData> = {
  queryKey: QueryKey
  listUrl: string
  detailUrl: (secretId: string) => string
  fetchSecrets: (listUrl: string, signal?: AbortSignal) => Promise<TData>
  createSecret: (listUrl: string, data: CreateSecretPayload) => Promise<SecretMutationResponse>
  updateSecret: (detailUrl: string, data: UpdateSecretPayload) => Promise<SecretMutationResponse>
  deleteSecret: (detailUrl: string) => Promise<{ ok: boolean; message: string }>
  showVisibilityToggle?: boolean
}

export function useSecretCrud<TData>({
  queryKey,
  listUrl,
  detailUrl,
  fetchSecrets,
  createSecret,
  updateSecret,
  deleteSecret,
  showVisibilityToggle = false,
}: UseSecretCrudOptions<TData>) {
  const queryClient = useQueryClient()
  const [modal, showModal] = useModal()
  const [banner, setBanner] = useState<string | null>(null)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const { data, isLoading, error } = useQuery<TData>({
    queryKey,
    queryFn: ({ signal }) => fetchSecrets(listUrl, signal),
  })

  const listError = error instanceof Error ? error.message : null

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey })
  }, [queryClient, queryKey])

  const handleSuccess = useCallback(
    (message: string) => {
      setBanner(message)
      setErrorBanner(null)
      refresh()
    },
    [refresh],
  )

  const handleCreate = useCallback(() => {
    showModal((onClose) => (
      <SecretFormModal
        showVisibilityToggle={showVisibilityToggle}
        onClose={onClose}
        onSubmit={async (formData) => {
          await createSecret(listUrl, formData as CreateSecretPayload)
          handleSuccess('Secret created.')
        }}
      />
    ))
  }, [createSecret, handleSuccess, listUrl, showModal, showVisibilityToggle])

  const handleEdit = useCallback(
    (secret: SecretDTO) => {
      showModal((onClose) => (
        <SecretFormModal
          editSecret={secret}
          onClose={onClose}
          onSubmit={async (formData) => {
            await updateSecret(detailUrl(secret.id), formData as UpdateSecretPayload)
            handleSuccess('Secret updated.')
          }}
        />
      ))
    },
    [detailUrl, handleSuccess, showModal, updateSecret],
  )

  const handleDelete = useCallback(
    (secret: SecretDTO) => {
      showModal((onClose) => (
        <DeleteSecretDialog
          secretName={secret.name}
          onClose={onClose}
          onConfirm={async () => {
            await deleteSecret(detailUrl(secret.id))
            handleSuccess('Secret deleted.')
          }}
        />
      ))
    },
    [deleteSecret, detailUrl, handleSuccess, showModal],
  )

  return {
    data,
    isLoading,
    listError,
    modal,
    banner,
    errorBanner,
    setErrorBanner,
    handleCreate,
    handleEdit,
    handleDelete,
    handleSuccess,
  }
}
