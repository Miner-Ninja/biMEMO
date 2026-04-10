/**
 * CSV export utilities
 */

/**
 * Convert table data to CSV string
 */
export function convertToCsv(columns: string[], data: Record<string, unknown>[]): string {
  if (columns.length === 0 || data.length === 0) {
    return ''
  }

  // Escape CSV value (handle quotes, commas, newlines)
  const escapeValue = (value: unknown): string => {
    if (value === null || value === undefined) {
      return ''
    }
    const str = String(value)
    // If contains comma, quote, or newline - wrap in quotes and escape internal quotes
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
      return `"${str.replace(/"/g, '""')}"`
    }
    return str
  }

  // Header row
  const headerRow = columns.map(escapeValue).join(',')

  // Data rows
  const dataRows = data.map((row) =>
    columns.map((col) => escapeValue(row[col])).join(',')
  )

  return [headerRow, ...dataRows].join('\n')
}

/**
 * Download CSV content as file
 */
export function downloadCsv(csv: string, filename: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

/**
 * Export table data to CSV file
 */
export function exportTableToCsv(
  columns: string[],
  data: Record<string, unknown>[],
  filename = 'export.csv'
): void {
  const csv = convertToCsv(columns, data)
  if (csv) {
    downloadCsv(csv, filename)
  }
}
