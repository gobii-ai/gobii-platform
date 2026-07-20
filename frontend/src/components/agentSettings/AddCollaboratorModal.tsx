import { CollaboratorInviteFormDialog } from './CollaboratorInviteFormDialog'

type AddCollaboratorModalProps = {
  onSubmit: (email: string) => Promise<void> | void
  onClose: () => void
}

export function AddCollaboratorModal({ onSubmit, onClose }: AddCollaboratorModalProps) {
  return (
    <CollaboratorInviteFormDialog
      open
      formId="collaborator-form"
      emailFieldId="collaborator-email-field"
      title="Invite Collaborator"
      subtitle="Invite an employee to chat and exchange files with this agent."
      onClose={onClose}
      onSubmit={onSubmit}
      submitLabel="Send Invite"
      submittingLabel="Saving…"
      cancelLabel="Cancel"
      closeOnSuccess
      autoFocus
    />
  )
}
