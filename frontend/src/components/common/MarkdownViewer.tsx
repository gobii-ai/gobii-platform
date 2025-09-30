import type { AnchorHTMLAttributes } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'

type MarkdownViewerProps = {
  content: string
  className?: string
}

const markdownComponents = {
  a: (props: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props} target={props.target ?? '_blank'} rel={props.rel ?? 'noopener noreferrer'} />
  ),
}

export function MarkdownViewer({ content, className }: MarkdownViewerProps) {
  return (
    <ReactMarkdown
      className={className}
      remarkPlugins={[remarkGfm as unknown as any, remarkBreaks as unknown as any]}
      components={markdownComponents}
    >
      {content}
    </ReactMarkdown>
  )
}
