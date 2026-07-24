import { useMemo, useState, type FormEvent } from 'react'
import { Fish, RotateCcw, Save, Trash2, Upload } from 'lucide-react'

import { safeErrorMessage } from '../../api/safeErrorMessage'
import type { UserPetSize } from '../../api/userPets'
import {
  useDeleteUserPet,
  useUpdateUserPet,
  useUpdateUserPetPreferences,
  useUploadUserPet,
  useUserPets,
} from '../../hooks/useUserPets'
import { PetSprite } from './PetSprite'
import './pets.css'

const PET_SIZE_LABELS: Record<UserPetSize, string> = {
  small: 'Small',
  medium: 'Medium',
  large: 'Large',
}

export function PetProfileSection() {
  const petsQuery = useUserPets()
  const preferencesMutation = useUpdateUserPetPreferences()
  const uploadMutation = useUploadUserPet()
  const updateMutation = useUpdateUserPet()
  const deleteMutation = useDeleteUserPet()
  const [uploadName, setUploadName] = useState('')
  const [uploadDescription, setUploadDescription] = useState('')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [editDraft, setEditDraft] = useState<{
    petId: string
    displayName: string
    description: string
  } | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const library = petsQuery.data
  const selectedPet = useMemo(() => (
    library?.pets.find((pet) => pet.id === library.preferences.selectedPetId)
      ?? library?.pets[0]
      ?? null
  ), [library])
  const customPetCount = library?.pets.filter((pet) => pet.kind === 'custom').length ?? 0

  const editName = selectedPet?.kind === 'custom' && editDraft?.petId === selectedPet.id
    ? editDraft.displayName
    : selectedPet?.kind === 'custom' ? selectedPet.displayName : ''
  const editDescription = selectedPet?.kind === 'custom' && editDraft?.petId === selectedPet.id
    ? editDraft.description
    : selectedPet?.kind === 'custom' ? selectedPet.description : ''

  async function patchPreferences(patch: Parameters<typeof preferencesMutation.mutateAsync>[0]) {
    setFeedback(null)
    setError(null)
    try {
      await preferencesMutation.mutateAsync(patch)
    } catch (requestError) {
      setError(safeErrorMessage(requestError, 'Unable to save pet preferences.'))
    }
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    if (!uploadFile || !uploadName.trim()) {
      setError('Choose a v2 WebP spritesheet and enter a pet name.')
      return
    }
    setFeedback(null)
    setError(null)
    try {
      const previousIds = new Set(library?.pets.map((pet) => pet.id) ?? [])
      const nextLibrary = await uploadMutation.mutateAsync({
        displayName: uploadName.trim(),
        description: uploadDescription.trim(),
        spritesheet: uploadFile,
      })
      const uploadedPet = nextLibrary.pets.find((pet) => !previousIds.has(pet.id))
      if (uploadedPet) {
        await preferencesMutation.mutateAsync({ selectedPetId: uploadedPet.id, enabled: true })
      }
      setUploadName('')
      setUploadDescription('')
      setUploadFile(null)
      form.reset()
      setFeedback('Custom pet uploaded and selected.')
    } catch (requestError) {
      setError(safeErrorMessage(requestError, 'Unable to upload that pet.'))
    }
  }

  async function handleSaveDetails() {
    if (!selectedPet || selectedPet.kind !== 'custom') return
    setFeedback(null)
    setError(null)
    try {
      await updateMutation.mutateAsync({
        petId: selectedPet.id,
        changes: {
          displayName: editName,
          description: editDescription,
        },
      })
      setEditDraft(null)
      setFeedback('Pet details saved.')
    } catch (requestError) {
      setError(safeErrorMessage(requestError, 'Unable to save pet details.'))
    }
  }

  async function handleDelete() {
    if (!selectedPet || selectedPet.kind !== 'custom') return
    if (!window.confirm(`Delete ${selectedPet.displayName}? This cannot be undone.`)) return
    setFeedback(null)
    setError(null)
    try {
      await deleteMutation.mutateAsync(selectedPet.id)
      setFeedback('Custom pet deleted.')
    } catch (requestError) {
      setError(safeErrorMessage(requestError, 'Unable to delete that pet.'))
    }
  }

  return (
    <section className="profile-screen__section pet-profile">
      <div className="profile-screen__section-header">
        <div className="profile-screen__section-icon" aria-hidden="true">
          <Fish className="h-4 w-4" />
        </div>
        <div>
          <h2>Workspace Pet</h2>
          <p>Choose the companion that appears in the immersive app.</p>
        </div>
      </div>

      {petsQuery.isLoading ? <p className="profile-screen__muted">Loading your pets…</p> : null}
      {petsQuery.isError ? (
        <p className="profile-screen__feedback profile-screen__feedback--error">
          {safeErrorMessage(petsQuery.error, 'Unable to load your pet library.')}
        </p>
      ) : null}

      {library && selectedPet ? (
        <>
          <div className="pet-profile__summary">
            <PetSprite
              spritesheetUrl={selectedPet.spritesheetUrl}
              row={0}
              column={0}
              className="pet-profile__preview"
              label={`${selectedPet.displayName} preview`}
            />
            <div className="pet-profile__summary-copy">
              <strong>{selectedPet.displayName}</strong>
              <span>{selectedPet.description || 'Custom Codex-compatible pet'}</span>
            </div>
            <label className="organization-screen__setting-toggle">
              <input
                type="checkbox"
                checked={library.preferences.enabled}
                onChange={(event) => void patchPreferences({ enabled: event.target.checked })}
                aria-label="Show workspace pet"
              />
              <span className="organization-screen__setting-switch" aria-hidden="true" />
            </label>
          </div>

          <div className="pet-profile__size-row">
            <span>Display size</span>
            <div className="pet-profile__segmented" aria-label="Pet display size">
              {(Object.keys(PET_SIZE_LABELS) as UserPetSize[]).map((size) => (
                <button
                  key={size}
                  type="button"
                  className="pet-profile__size-button"
                  data-selected={library.preferences.size === size ? 'true' : 'false'}
                  onClick={() => void patchPreferences({ size })}
                >
                  {PET_SIZE_LABELS[size]}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="profile-screen__button profile-screen__button--secondary"
              onClick={() => void patchPreferences({ position: null })}
            >
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              Reset Position
            </button>
          </div>

          <div className="pet-profile__library" aria-label="Pet library">
            {library.pets.map((pet) => (
              <button
                key={pet.id}
                type="button"
                className="pet-profile__pet-option"
                data-selected={pet.id === selectedPet.id ? 'true' : 'false'}
                onClick={() => void patchPreferences({ selectedPetId: pet.id, enabled: true })}
              >
                <PetSprite
                  spritesheetUrl={pet.spritesheetUrl}
                  row={0}
                  column={0}
                  className="pet-profile__option-preview"
                />
                <span>{pet.displayName}</span>
                <small>{pet.kind === 'builtin' ? 'Included' : 'Custom'}</small>
              </button>
            ))}
          </div>

          {selectedPet.kind === 'custom' ? (
            <div className="pet-profile__custom-details">
              <label className="profile-screen__field">
                <span>Pet Name</span>
                <input
                  value={editName}
                  maxLength={80}
                  onChange={(event) => setEditDraft({
                    petId: selectedPet.id,
                    displayName: event.target.value,
                    description: editDescription,
                  })}
                />
              </label>
              <label className="profile-screen__field">
                <span>Description</span>
                <input
                  value={editDescription}
                  maxLength={240}
                  onChange={(event) => setEditDraft({
                    petId: selectedPet.id,
                    displayName: editName,
                    description: event.target.value,
                  })}
                />
              </label>
              <div className="profile-screen__button-row">
                <button
                  type="button"
                  className="profile-screen__button profile-screen__button--primary"
                  onClick={() => void handleSaveDetails()}
                  disabled={updateMutation.isPending || !editName.trim()}
                >
                  <Save className="h-4 w-4" aria-hidden="true" />
                  Save Pet
                </button>
                <button
                  type="button"
                  className="profile-screen__button profile-screen__button--danger"
                  onClick={() => void handleDelete()}
                  disabled={deleteMutation.isPending}
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                  Delete Pet
                </button>
              </div>
            </div>
          ) : null}

          {customPetCount < library.maxCustomPets ? (
            <form className="pet-profile__upload" onSubmit={(event) => void handleUpload(event)}>
              <div>
                <strong>Upload a custom pet</strong>
                <p className="profile-screen__muted">
                  {customPetCount} of {library.maxCustomPets} slots used. Use a transparent Codex v2
                  WebP: 1536×2288 pixels and no more than 4 MB.
                </p>
              </div>
              <div className="profile-screen__form-grid">
                <label className="profile-screen__field">
                  <span>Pet Name</span>
                  <input value={uploadName} maxLength={80} onChange={(event) => setUploadName(event.target.value)} />
                </label>
                <label className="profile-screen__field">
                  <span>Description</span>
                  <input
                    value={uploadDescription}
                    maxLength={240}
                    onChange={(event) => setUploadDescription(event.target.value)}
                  />
                </label>
                <label className="profile-screen__field profile-screen__field--wide">
                  <span>V2 WebP Spritesheet</span>
                  <input
                    type="file"
                    accept=".webp,image/webp"
                    onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
                  />
                </label>
              </div>
              <button
                type="submit"
                className="profile-screen__button profile-screen__button--secondary"
                disabled={uploadMutation.isPending || !uploadFile || !uploadName.trim()}
              >
                <Upload className="h-4 w-4" aria-hidden="true" />
                {uploadMutation.isPending ? 'Uploading…' : `Upload Pet (${customPetCount}/${library.maxCustomPets})`}
              </button>
            </form>
          ) : (
            <p className="profile-screen__muted">
              Your custom pet library is full. Delete one to upload another.
            </p>
          )}

          {feedback ? <p className="profile-screen__feedback profile-screen__feedback--success">{feedback}</p> : null}
          {error ? <p className="profile-screen__feedback profile-screen__feedback--error">{error}</p> : null}
        </>
      ) : null}
    </section>
  )
}
