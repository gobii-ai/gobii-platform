import { LlmConfigView } from '../components/llmConfig/LlmConfigView'
import { useLlmConfigController } from '../components/llmConfig/useLlmConfigController'

export function LlmConfigScreen() {
  const controller = useLlmConfigController()
  return <LlmConfigView controller={controller} />
}
