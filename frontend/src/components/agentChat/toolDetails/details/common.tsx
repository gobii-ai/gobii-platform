import { MarkdownViewer } from '../../../common/MarkdownViewer'
import { StructuredDataTable } from '../../../common/StructuredDataTable'
import { looksLikeHtml, sanitizeHtml } from '../../../../util/sanitize'
import type { ToolDetailProps } from '../../tooling/types'
import { createNormalizeContext, normalizeStructuredValue, tryParseJson } from '../normalize'
import { KeyValueList, Section } from '../shared'
import { isNonEmptyString } from '../utils'

function useToolData(entry: ToolDetailProps['entry']) {
  const parameters =
    entry.parameters && typeof entry.parameters === 'object' && !Array.isArray(entry.parameters)
      ? (entry.parameters as Record<string, unknown>)
      : null
  const showParameters = Boolean(parameters && Object.keys(parameters).length > 0)
  const normalizedParameters = parameters
    ? (normalizeStructuredValue(parameters, createNormalizeContext()) as Record<string, unknown>)
    : null

  const stringResult = typeof entry.result === 'string' ? entry.result.trim() : null
  const htmlResult = stringResult && looksLikeHtml(stringResult) ? sanitizeHtml(stringResult) : null
  const objectResult =
    entry.result && typeof entry.result === 'object'
      ? (entry.result as Record<string, unknown> | unknown[])
      : null
  const parsedJsonResult = stringResult ? tryParseJson(stringResult) : null
  const structuredResult = objectResult ?? parsedJsonResult
  const normalizedStructuredResult =
    structuredResult !== null && structuredResult !== undefined
      ? normalizeStructuredValue(structuredResult, createNormalizeContext())
      : null
  const hasStructuredResult = normalizedStructuredResult !== null && normalizedStructuredResult !== undefined
  const showStringResult = Boolean(stringResult && !parsedJsonResult)

  return {
    parameters,
    showParameters,
    normalizedParameters,
    stringResult,
    htmlResult,
    objectResult,
    structuredResult,
    normalizedStructuredResult,
    hasStructuredResult,
    showStringResult,
  }
}

export function GenericToolDetail({ entry }: ToolDetailProps) {
  const {
    parameters,
    showParameters,
    normalizedParameters,
    stringResult,
    htmlResult,
    structuredResult,
    normalizedStructuredResult,
    hasStructuredResult,
    showStringResult,
  } = useToolData(entry)

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList
        items={[
          entry.toolName ? { label: 'Tool', value: entry.toolName } : null,
          entry.summary ? { label: 'Summary', value: entry.summary } : null,
        ]}
      />
      {showParameters ? (
        <Section title="Parameters">
          <StructuredDataTable value={normalizedParameters ?? parameters} />
        </Section>
      ) : null}
      {showStringResult && stringResult ? (
        <Section title="Result">
          {htmlResult ? (
            <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: htmlResult }} />
          ) : (
            <MarkdownViewer content={stringResult} className="prose prose-sm max-w-none" />
          )}
        </Section>
      ) : null}
      {hasStructuredResult ? (
        <Section title="Result">
          <StructuredDataTable value={normalizedStructuredResult ?? structuredResult} />
        </Section>
      ) : null}
    </div>
  )
}

export function UpdateCharterDetail({ entry }: ToolDetailProps) {
  const charter =
    entry.charterText ||
    (entry.parameters?.new_charter as string | undefined) ||
    (entry.parameters?.charter as string | undefined)
  const summary = isNonEmptyString(entry.summary) ? entry.summary : null
  const charterMarkdown = isNonEmptyString(charter) ? charter : null
  return (
    <div className="space-y-4 text-sm text-slate-600">
      <p className="text-slate-700">{summary ?? 'The agent assignment was updated.'}</p>
      {charterMarkdown ? (
        <Section title="Updated Charter">
          <MarkdownViewer content={charterMarkdown} className="prose prose-sm max-w-none" />
        </Section>
      ) : null}
    </div>
  )
}

export function McpToolDetail({ entry }: ToolDetailProps) {
  const {
    parameters,
    showParameters,
    normalizedParameters,
    stringResult,
    htmlResult,
    structuredResult,
    normalizedStructuredResult,
    hasStructuredResult,
    showStringResult,
  } = useToolData(entry)

  const infoItems = [
    entry.mcpInfo?.serverLabel ? { label: 'Server', value: entry.mcpInfo.serverLabel } : null,
    entry.mcpInfo?.toolLabel ? { label: 'Tool', value: entry.mcpInfo.toolLabel } : null,
    entry.summary ? { label: 'Summary', value: entry.summary } : null,
  ]

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList items={infoItems} />
      {showParameters ? (
        <Section title="Parameters">
          <StructuredDataTable value={normalizedParameters ?? parameters} />
        </Section>
      ) : null}
      {showStringResult && stringResult ? (
        <Section title="Result">
          {htmlResult ? (
            <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: htmlResult }} />
          ) : (
            <MarkdownViewer content={stringResult} className="prose prose-sm max-w-none" />
          )}
        </Section>
      ) : null}
      {hasStructuredResult ? (
        <Section title="Result">
          <StructuredDataTable value={normalizedStructuredResult ?? structuredResult} />
        </Section>
      ) : null}
    </div>
  )
}
