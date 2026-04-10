import { Download } from 'lucide-react'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import type { QueryResult } from '@/types/chat'
import { cn } from '@/lib/utils'
import { exportTableToCsv } from '@/lib/csv-export'

interface ResultsTableProps {
  results: QueryResult
  className?: string
}

/**
 * Format a cell value for display
 */
function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (value instanceof Date) return value.toLocaleString()
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/**
 * Table display for query results
 */
export function ResultsTable({ results, className }: ResultsTableProps) {
  const { columns, data, rowCount } = results

  const handleExport = () => {
    const timestamp = new Date().toISOString().slice(0, 19).replace(/[:-]/g, '')
    exportTableToCsv(columns, data, `query-results-${timestamp}.csv`)
  }

  if (data.length === 0) {
    return (
      <div className={cn('p-4 text-center text-muted-foreground', className)}>
        No results found
      </div>
    )
  }

  return (
    <div className={cn('space-y-2', className)}>
      <div data-slot="table-container" className="w-full overflow-x-auto rounded border" style={{ contain: 'inline-size' }}>
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((column) => (
                <TableHead key={column} className="whitespace-nowrap font-medium">
                  {column}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((row, rowIndex) => {
              // Create a stable key from row content, fallback to index if all values are same
              const rowKey = columns.map(col => String(row[col] ?? '')).join('|') || String(rowIndex)
              return (
                <TableRow key={rowKey}>
                  {columns.map((column) => (
                    <TableCell key={column} className="whitespace-nowrap">
                      {formatCellValue(row[column])}
                    </TableCell>
                  ))}
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          {rowCount} {rowCount === 1 ? 'row' : 'rows'}
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleExport}
          className="h-7 gap-1 text-xs"
        >
          <Download className="h-3 w-3" />
          Export CSV
        </Button>
      </div>
    </div>
  )
}
