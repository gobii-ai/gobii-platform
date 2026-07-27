import { useState, type FormEvent } from 'react'
import { Fish, Upload } from 'lucide-react'

import { safeErrorMessage } from '../../api/safeErrorMessage'
import { useUploadUserPet } from '../../hooks/useUserPets'
import { FormField, TextareaInput, TextInput } from '../common/FormControls'
import { ModalForm } from '../common/ModalForm'

type AddPetModalProps = {
  customPetCount: number
  maxCustomPets: number
  onClose: () => void
  onUploaded: (displayName: string) => void
}

export function AddPetModal({
  customPetCount,
  maxCustomPets,
  onClose,
  onUploaded,
}: AddPetModalProps) {
  const uploadMutation = useUploadUserPet()
  const [displayName, setDisplayName] = useState('')
  const [description, setDescription] = useState('')
  const [spritesheet, setSpritesheet] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const submitting = uploadMutation.isPending

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!spritesheet || !displayName.trim()) return

    setError(null)
    try {
      await uploadMutation.mutateAsync({
        displayName: displayName.trim(),
        description: description.trim(),
        spritesheet,
      })
      onUploaded(displayName.trim())
    } catch (requestError) {
      setError(safeErrorMessage(requestError, 'Unable to upload that pet.'))
    }
  }

  return (
    <ModalForm
      id="add-pet-form"
      title="Add new pet"
      subtitle="Create a custom chat companion from a Codex v2 spritesheet."
      icon={Fish}
      iconBgClass="bg-sky-100"
      iconColorClass="text-sky-700"
      widthClass="sm:max-w-xl"
      onClose={onClose}
      dismissible={!submitting}
      onSubmit={handleSubmit}
      submitLabel="Add pet"
      submittingLabel="Adding pet…"
      submitting={submitting}
      submitDisabled={!displayName.trim() || !spritesheet}
      errorMessages={error ? [error] : null}
      formClassName="space-y-5"
      autoComplete="off"
    >
      <FormField id="add-pet-name" label="Pet name">
        <TextInput
          id="add-pet-name"
          value={displayName}
          maxLength={80}
          autoFocus
          required
          onChange={(event) => setDisplayName(event.currentTarget.value)}
          placeholder="Give your companion a name"
          disabled={submitting}
        />
      </FormField>

      <FormField
        id="add-pet-description"
        label={<>Description <span className="text-slate-400">(optional)</span></>}
      >
        <TextareaInput
          id="add-pet-description"
          value={description}
          maxLength={240}
          rows={3}
          onChange={(event) => setDescription(event.currentTarget.value)}
          placeholder="Describe your pet’s personality"
          disabled={submitting}
        />
      </FormField>

      <FormField
        id="add-pet-spritesheet"
        label="V2 WebP spritesheet"
        helpText={`${customPetCount} of ${maxCustomPets} custom pet slots used. Spritesheets must be transparent, 1536×2288 pixels, and no more than 4 MB.`}
      >
        <input
          id="add-pet-spritesheet"
          type="file"
          accept=".webp,image/webp"
          className="sr-only"
          onChange={(event) => setSpritesheet(event.currentTarget.files?.[0] ?? null)}
          disabled={submitting}
          required
        />
        <label
          htmlFor="add-pet-spritesheet"
          className="mt-1 flex min-h-12 cursor-pointer items-center gap-3 rounded-xl border border-dashed border-slate-300 bg-white px-4 py-3 text-sm text-slate-600 transition hover:border-sky-400 hover:text-sky-700"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sky-50 text-sky-700">
            <Upload className="h-4 w-4" aria-hidden="true" />
          </span>
          <span className="min-w-0 truncate">
            {spritesheet?.name ?? 'Choose a WebP spritesheet'}
          </span>
        </label>
      </FormField>
    </ModalForm>
  )
}
