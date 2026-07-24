import { useEffect, useMemo, useRef, useState } from 'react'
import { Fish, Plus, RotateCcw, Save, Trash2 } from 'lucide-react'

import { safeErrorMessage } from '../../api/safeErrorMessage'
import type { UserPetSize } from '../../api/userPets'
import {
  useDeleteUserPet,
  useUpdateUserPet,
  useUpdateUserPetPreferences,
  useUserPets,
} from '../../hooks/useUserPets'
import { AddPetModal } from './AddPetModal'
import { PetSprite } from './PetSprite'
import './pets.css'

const PET_SIZE_LABELS: Record<UserPetSize, string> = {
  small: 'Small',
  medium: 'Medium',
  large: 'Large',
}

export function PetProfileSection() {
  const sectionRef = useRef<HTMLElement | null>(null)
  const petsQuery = useUserPets()
  const preferencesMutation = useUpdateUserPetPreferences()
  const updateMutation = useUpdateUserPet()
  const deleteMutation = useDeleteUserPet()
  const [addPetOpen, setAddPetOpen] = useState(false)
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

  useEffect(() => {
    const revealPetOptions = () => {
      if (window.location.hash !== '#workspace-pet') return
      window.requestAnimationFrame(() => {
        sectionRef.current?.scrollIntoView({ block: 'start' })
      })
    }

    revealPetOptions()
    window.addEventListener('popstate', revealPetOptions)
    window.addEventListener('hashchange', revealPetOptions)
    return () => {
      window.removeEventListener('popstate', revealPetOptions)
      window.removeEventListener('hashchange', revealPetOptions)
    }
  }, [])

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
    <section id="workspace-pet" ref={sectionRef} className="profile-screen__section pet-profile">
      <div className="profile-screen__section-header">
        <div className="profile-screen__section-icon" aria-hidden="true">
          <Fish className="h-4 w-4" />
        </div>
        <div>
          <h2>Workspace Pet</h2>
          <p>Choose your chat companion</p>
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
            <button
              type="button"
              className="pet-profile__pet-option pet-profile__pet-option--add"
              onClick={() => {
                setFeedback(null)
                setError(null)
                setAddPetOpen(true)
              }}
              disabled={customPetCount >= library.maxCustomPets}
              aria-label={customPetCount >= library.maxCustomPets ? 'Custom pet library full' : 'Add new pet'}
            >
              <span className="pet-profile__add-icon" aria-hidden="true">
                <Plus className="h-5 w-5" />
              </span>
              <span>Add new</span>
              <small>
                {customPetCount >= library.maxCustomPets
                  ? 'Library full'
                  : `${customPetCount} of ${library.maxCustomPets}`}
              </small>
            </button>
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

          {feedback ? <p className="profile-screen__feedback profile-screen__feedback--success">{feedback}</p> : null}
          {error ? <p className="profile-screen__feedback profile-screen__feedback--error">{error}</p> : null}
        </>
      ) : null}
      {addPetOpen && library ? (
        <AddPetModal
          existingPetIds={library.pets.map((pet) => pet.id)}
          customPetCount={customPetCount}
          maxCustomPets={library.maxCustomPets}
          onClose={() => setAddPetOpen(false)}
          onUploaded={(displayName) => {
            setAddPetOpen(false)
            setFeedback(`${displayName} was added and selected.`)
            setError(null)
          }}
        />
      ) : null}
    </section>
  )
}
