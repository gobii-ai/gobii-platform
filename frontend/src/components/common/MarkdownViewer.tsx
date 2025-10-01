import type {
  AnchorHTMLAttributes,
  DetailedHTMLProps,
  HTMLAttributes,
  TdHTMLAttributes,
  ThHTMLAttributes,
} from 'react'
import ReactMarkdown from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

import 'highlight.js/styles/github.css'

type MarkdownViewerProps = {
  content: string
  className?: string
}

const markdownComponents = {
  a: (props: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props} target={props.target ?? '_blank'} rel={props.rel ?? 'noopener noreferrer'} />
  ),
  table: ({ className = '', children, ...rest }: DetailedHTMLProps<HTMLAttributes<HTMLTableElement>, HTMLTableElement>) => (
    <div className="my-4 overflow-x-auto">
      <table
        {...rest}
        className={`min-w-full border-collapse text-left text-sm ${className}`.trim()}
      >
        {children}
      </table>
    </div>
  ),
  thead: ({ className = '', children, ...rest }: DetailedHTMLProps<HTMLAttributes<HTMLTableSectionElement>, HTMLTableSectionElement>) => (
    <thead {...rest} className={`bg-slate-100/60 text-xs uppercase tracking-wide text-slate-600 ${className}`.trim()}>
      {children}
    </thead>
  ),
  tbody: ({ className = '', children, ...rest }: DetailedHTMLProps<HTMLAttributes<HTMLTableSectionElement>, HTMLTableSectionElement>) => (
    <tbody {...rest} className={`divide-y divide-slate-200 bg-white ${className}`.trim()}>
      {children}
    </tbody>
  ),
  tr: ({ className = '', children, ...rest }: DetailedHTMLProps<HTMLAttributes<HTMLTableRowElement>, HTMLTableRowElement>) => (
    <tr {...rest} className={`hover:bg-slate-50 ${className}`.trim()}>
      {children}
    </tr>
  ),
  th: ({ className = '', children, ...rest }: DetailedHTMLProps<ThHTMLAttributes<HTMLTableCellElement>, HTMLTableCellElement>) => (
    <th {...rest} className={`px-3 py-2 font-semibold ${className}`.trim()}>
      {children}
    </th>
  ),
  td: ({ className = '', children, ...rest }: DetailedHTMLProps<TdHTMLAttributes<HTMLTableCellElement>, HTMLTableCellElement>) => (
    <td {...rest} className={`px-3 py-2 align-top text-slate-700 ${className}`.trim()}>
      {children}
    </td>
  ),
  pre: ({ className = '', children, ...rest }: DetailedHTMLProps<HTMLAttributes<HTMLPreElement>, HTMLPreElement>) => (
    <pre
      {...rest}
      className={`my-3 overflow-x-auto rounded-md bg-slate-900/90 p-4 text-[13px] text-slate-100 shadow ${className}`.trim()}
    >
      {children}
    </pre>
  ),
  code: (props: DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement>) => {
    const { className = '', children, ...rest } = props
    const isInline = !/^language-/.test(className)
    if (isInline) {
      return (
        <code {...rest} className={`rounded bg-slate-900/10 px-1.5 py-0.5 font-mono text-xs ${className}`}>
          {children}
        </code>
      )
    }
    return (
      <code {...rest} className={`${className} font-mono text-sm`}>
        {children}
      </code>
    )
  },
}

export function MarkdownViewer({ content, className }: MarkdownViewerProps) {
  return (
    <ReactMarkdown
      className={className}
      remarkPlugins={[remarkGfm as unknown as any, remarkBreaks as unknown as any]}
      rehypePlugins={[rehypeHighlight as unknown as any]}
      components={markdownComponents}
    >
      {content}
    </ReactMarkdown>
  )
}
