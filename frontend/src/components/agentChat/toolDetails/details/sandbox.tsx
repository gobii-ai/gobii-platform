import type { ToolDetailProps } from '../../tooling/types'
import { parseResultObject } from '../../../../util/objectUtils'
import { CodeBlock, JsonBlock, KeyValueList, OutputBlock, Section } from '../shared'
import { isNonEmptyString } from '../utils'

function toText(value: unknown): string | null {
  return isNonEmptyString(value) ? value.trim() : null
}

function toContent(value: unknown): string | null {
  return isNonEmptyString(value) ? value : null
}

function toInteger(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  if (typeof value === 'string' && value.trim().length) {
    const parsed = Number.parseInt(value, 10)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function collectExecutionResult(entry: ToolDetailProps['entry']) {
  const result = parseResultObject(entry.result)
  return {
    status: toText(result?.status) || entry.status || null,
    message: toText(result?.message) || null,
    stdout: toContent(result?.stdout) || null,
    stderr: toContent(result?.stderr) || null,
    exitCode: toInteger(result?.exit_code),
  }
}

function renderCustomToolValue(value: unknown) {
  if (Array.isArray(value) || (value !== null && typeof value === 'object')) {
    return <JsonBlock value={value as Record<string, unknown> | unknown[]} />
  }
  if (typeof value === 'string' && value.trim().length) {
    return <OutputBlock content={value} />
  }
  if (value === null || value === undefined) {
    return <p className="text-slate-500">No result returned.</p>
  }
  return (
    <p className="font-mono text-xs text-slate-700">{String(value)}</p>
  )
}

export function RunCommandDetail({ entry }: ToolDetailProps) {
  const params = entry.parameters || {}
  const command = toContent(params.command)
  const cwd = toText(params.cwd)
  const timeout = toInteger(params.timeout_seconds)
  const { status, message, stdout, stderr, exitCode } = collectExecutionResult(entry)

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList
        items={[
          status ? { label: 'Status', value: status.toUpperCase() } : null,
          exitCode !== null ? { label: 'Exit code', value: String(exitCode) } : null,
          cwd ? { label: 'Working dir', value: cwd } : null,
          timeout !== null ? { label: 'Timeout', value: `${timeout}s` } : null,
        ]}
      />
      {command ? (
        <Section title="Command">
          <CodeBlock code={command} language="bash" />
        </Section>
      ) : null}
      {message && message !== stderr ? <p className="text-slate-700">{message}</p> : null}
      {stdout ? (
        <Section title="Output">
          <OutputBlock content={stdout} />
        </Section>
      ) : null}
      {stderr ? (
        <Section title="Errors">
          <OutputBlock content={stderr} className="text-rose-100" />
        </Section>
      ) : null}
    </div>
  )
}

export function PythonExecDetail({ entry }: ToolDetailProps) {
  const params = entry.parameters || {}
  const code = toContent(params.code)
  const { message, stdout, stderr } = collectExecutionResult(entry)

  return (
    <div className="space-y-3 text-sm text-slate-600">
      {code ? (
        <Section title="Python">
          <CodeBlock code={code} language="python" />
        </Section>
      ) : null}
      {message && message !== stderr ? <p className="text-slate-700">{message}</p> : null}
      {stdout ? (
        <Section title="Output">
          <OutputBlock content={stdout} />
        </Section>
      ) : null}
      {stderr ? (
        <Section title="Errors">
          <OutputBlock content={stderr} className="text-rose-100" />
        </Section>
      ) : null}
    </div>
  )
}

export function FileStringReplaceDetail({ entry }: ToolDetailProps) {
  const params = entry.parameters || {}
  const result = parseResultObject(entry.result)
  const path = toText(params.path) || toText(result?.path)
  const oldText = toContent(params.old_text)
  const newTextValue = typeof params.new_text === 'string' ? params.new_text : null

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList
        items={[
          path ? { label: 'Path', value: path } : null,
        ]}
      />
      {oldText ? (
        <Section title="Find">
          <CodeBlock code={oldText} language="text" />
        </Section>
      ) : null}
      <Section title="Replace With">
        {newTextValue && newTextValue.length ? (
          <CodeBlock code={newTextValue} language="text" />
        ) : (
          <p className="text-slate-700">Empty string. Matching text is removed.</p>
        )}
      </Section>
    </div>
  )
}

export function CreateCustomToolDetail({ entry }: ToolDetailProps) {
  const params = entry.parameters || {}
  const result = parseResultObject(entry.result)
  const name = toText(result?.name) || toText(params.name)
  const sourcePath = toText(result?.source_path) || toText(params.source_path)
  const description = toText(params.description)
  const sourceCode = toContent(params.source_code)
  const schema =
    params.parameters_schema && typeof params.parameters_schema === 'object' && !Array.isArray(params.parameters_schema)
      ? (params.parameters_schema as Record<string, unknown>)
      : null

  return (
    <div className="space-y-3 text-sm text-slate-600">
      <KeyValueList
        items={[
          name ? { label: 'Name', value: name } : null,
          sourcePath ? { label: 'Source', value: sourcePath } : null,
        ]}
      />
      {description ? <p className="text-slate-700">{description}</p> : null}
      {schema ? (
        <Section title="Parameters">
          <JsonBlock value={schema} />
        </Section>
      ) : null}
      {sourceCode ? (
        <Section title="Python Source">
          <CodeBlock code={sourceCode} language="python" />
        </Section>
      ) : null}
    </div>
  )
}

export function CustomToolRunDetail({ entry }: ToolDetailProps) {
  const params =
    entry.parameters && typeof entry.parameters === 'object' && !Array.isArray(entry.parameters)
      ? (entry.parameters as Record<string, unknown>)
      : null
  const result = parseResultObject(entry.result)
  const nestedResult = result && 'result' in result ? result.result : entry.result
  const stdout = toContent(result?.stdout)
  const stderr = toContent(result?.stderr)
  const message = toText(result?.message)
  const hasParams = Boolean(params && Object.keys(params).length > 0)

  return (
    <div className="space-y-3 text-sm text-slate-600">
      {hasParams ? (
        <Section title="Parameters">
          <JsonBlock value={params!} />
        </Section>
      ) : null}
      <Section title="Result">
        {renderCustomToolValue(nestedResult)}
      </Section>
      {message ? <p className="text-slate-700">{message}</p> : null}
      {stdout ? (
        <Section title="Output">
          <OutputBlock content={stdout} />
        </Section>
      ) : null}
      {stderr ? (
        <Section title="Errors">
          <OutputBlock content={stderr} className="text-rose-100" />
        </Section>
      ) : null}
    </div>
  )
}
