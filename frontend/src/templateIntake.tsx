import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { IntakeBriefCard, type IntakeSchema } from './components/templateIntake/IntakeBriefCard'

const mount = document.getElementById('template-intake-root')

if (mount) {
  const schema = JSON.parse(mount.dataset.schema ?? '{}') as IntakeSchema
  const action = mount.dataset.action ?? ''
  const csrf = mount.dataset.csrf ?? ''

  const submit = (answers: Record<string, string | string[]>) => {
    const form = document.createElement('form')
    form.method = 'POST'
    form.action = action
    const csrfInput = document.createElement('input')
    csrfInput.type = 'hidden'
    csrfInput.name = 'csrfmiddlewaretoken'
    csrfInput.value = csrf
    const answersInput = document.createElement('input')
    answersInput.type = 'hidden'
    answersInput.name = 'answers'
    answersInput.value = JSON.stringify(answers)
    form.append(csrfInput, answersInput)
    document.body.append(form)
    form.submit()
  }

  createRoot(mount).render(
    <StrictMode>
      <IntakeBriefCard schema={schema} onSubmit={submit} />
    </StrictMode>,
  )
}
