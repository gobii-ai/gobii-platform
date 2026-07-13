import type {
  HTMLAttributes,
  ReactNode,
  TdHTMLAttributes,
  ThHTMLAttributes,
} from 'react'
import {
  flexRender,
  type Cell,
  type Header,
  type Row,
  type Table,
} from '@tanstack/react-table'

export type TanStackTableBodyMessage = {
  content: ReactNode
  cellClassName?: string
}

type TanStackTableShellProps<TData> = {
  table: Table<TData>
  rows?: Row<TData>[]
  bodyState?: TanStackTableBodyMessage | null
  emptyState?: TanStackTableBodyMessage | null
  leadingRows?: ReactNode | ((columnCount: number) => ReactNode)
  tableClassName?: string
  headClassName?: string
  headerRowClassName?: string
  headerCellClassName?: string
  bodyClassName?: string
  rowClassName?: string
  cellClassName?: string
  getHeaderCellProps?: (
    header: Header<TData, unknown>,
  ) => ThHTMLAttributes<HTMLTableCellElement> | undefined
  getRowProps?: (
    row: Row<TData>,
  ) => HTMLAttributes<HTMLTableRowElement> | undefined
  getCellProps?: (
    cell: Cell<TData, unknown>,
  ) => TdHTMLAttributes<HTMLTableCellElement> | undefined
}

function joinClassNames(...values: Array<string | undefined>): string {
  return values.filter(Boolean).join(' ')
}

export function TanStackTableShell<TData>({
  table,
  rows = table.getRowModel().rows,
  bodyState = null,
  emptyState = null,
  leadingRows = null,
  tableClassName = 'min-w-full border-collapse',
  headClassName = 'bg-slate-950/45',
  headerRowClassName = 'border-b border-slate-200/15',
  headerCellClassName = 'px-4 py-3 text-left align-middle',
  bodyClassName = 'bg-transparent',
  rowClassName = 'border-b border-slate-200/10 last:border-b-0',
  cellClassName = 'px-4 py-4 align-middle',
  getHeaderCellProps,
  getRowProps,
  getCellProps,
}: TanStackTableShellProps<TData>) {
  const columnCount = table.getVisibleLeafColumns().length
  const resolvedLeadingRows = typeof leadingRows === 'function'
    ? leadingRows(columnCount)
    : leadingRows
  const message = bodyState ?? (rows.length === 0 ? emptyState : null)

  return (
    <table className={tableClassName}>
      <thead className={headClassName}>
        {table.getHeaderGroups().map((headerGroup) => (
          <tr key={headerGroup.id} className={headerRowClassName}>
            {headerGroup.headers.map((header) => {
              const {
                className,
                ...headerCellProps
              } = getHeaderCellProps?.(header as Header<TData, unknown>) ?? {}
              return (
                <th
                  key={header.id}
                  scope="col"
                  {...headerCellProps}
                  className={joinClassNames(headerCellClassName, className)}
                >
                  {header.isPlaceholder
                    ? null
                    : flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              )
            })}
          </tr>
        ))}
      </thead>
      <tbody className={bodyClassName}>
        {bodyState ? null : resolvedLeadingRows}
        {message ? (
          <tr>
            <td
              colSpan={columnCount}
              className={message.cellClassName ?? 'px-4 py-10 text-center text-sm text-slate-300'}
            >
              {message.content}
            </td>
          </tr>
        ) : (
          rows.map((row) => {
            const {
              className,
              ...rowProps
            } = getRowProps?.(row) ?? {}
            return (
              <tr
                key={row.id}
                {...rowProps}
                className={joinClassNames(rowClassName, className)}
              >
                {row.getVisibleCells().map((cell) => {
                  const {
                    className: resolvedCellClassName,
                    ...cellProps
                  } = getCellProps?.(cell as Cell<TData, unknown>) ?? {}
                  return (
                    <td
                      key={cell.id}
                      {...cellProps}
                      className={joinClassNames(cellClassName, resolvedCellClassName)}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  )
                })}
              </tr>
            )
          })
        )}
      </tbody>
    </table>
  )
}
