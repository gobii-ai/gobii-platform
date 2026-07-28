import { Fish } from 'lucide-react'

import { EmbeddedAgentShellBackButton } from './EmbeddedAgentShellBackButton'
import { EmbeddedAgentShellPanel } from './EmbeddedAgentShellPanel'
import { PetOptionsPanel } from '../pets/PetOptionsPanel'

type EmbeddedWorkspacePetPanelProps = {
  onBack?: () => void
}

/**
 * The profile page's Workspace Pet section, unchanged, rendered in the chat shell the
 * same way agent settings are (#481): configuring the pet must not take the user away
 * from their active conversation, and the settings themselves must look exactly as they
 * do on the profile page.
 */
export function EmbeddedWorkspacePetPanel({ onBack }: EmbeddedWorkspacePetPanelProps) {
  return (
    <EmbeddedAgentShellPanel>
      <div className="flex flex-col gap-4 p-4">
        <div>
          <EmbeddedAgentShellBackButton onClick={onBack} ariaLabel="Back to chat" />
        </div>
        <section className="profile-screen__section pet-profile">
          <div className="profile-screen__section-header">
            <div className="profile-screen__section-icon" aria-hidden="true">
              <Fish className="h-4 w-4" />
            </div>
            <div>
              <h2>Workspace Pet</h2>
              <p>Choose your chat companion</p>
            </div>
          </div>
          <PetOptionsPanel />
        </section>
      </div>
    </EmbeddedAgentShellPanel>
  )
}
