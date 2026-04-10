export interface ChartData {
  type: 'bar' | 'line' | 'pie'
  title: string
  x: string
  y: string
  data: Record<string, unknown>[]
}

const DATE_PATTERNS = /^(date|time|month|year|period|день|дата|месяц|год|период|data|mėnuo|metai)/i

/**
 * Detect if a column contains date-like values.
 */
function isDateColumn(name: string, values: unknown[]): boolean {
  if (DATE_PATTERNS.test(name)) return true
  // Check if values look like dates (YYYY-MM, YYYY-MM-DD, etc.)
  const sample = values.slice(0, 5)
  return sample.every((v) => typeof v === 'string' && /^\d{4}[-/]/.test(v))
}

/**
 * Detect appropriate chart type from table columns and data.
 * Returns null if data is not suitable for charting.
 */
export function detectChartType(
  columns: string[],
  data: Record<string, unknown>[]
): ChartData | null {
  if (!data.length || data.length > 50) return null

  const textCols: string[] = []
  const numCols: string[] = []
  const dateCols: string[] = []

  for (const col of columns) {
    const values = data.map((row) => row[col])
    const nonNull = values.filter((v) => v != null)
    if (!nonNull.length) continue

    if (isDateColumn(col, nonNull)) {
      dateCols.push(col)
    } else if (nonNull.every((v) => typeof v === 'number')) {
      numCols.push(col)
    } else {
      textCols.push(col)
    }
  }

  // Date column + numeric → line chart
  if (dateCols.length >= 1 && numCols.length >= 1) {
    return {
      type: 'line',
      title: '',
      x: dateCols[0],
      y: numCols[0],
      data,
    }
  }

  // 1 text + 1 numeric, few records → pie
  if (textCols.length === 1 && numCols.length === 1 && data.length <= 8) {
    return {
      type: 'pie',
      title: '',
      x: textCols[0],
      y: numCols[0],
      data,
    }
  }

  // 1 text + 1+ numeric → bar
  if (textCols.length >= 1 && numCols.length >= 1) {
    return {
      type: 'bar',
      title: '',
      x: textCols[0],
      y: numCols[0],
      data,
    }
  }

  return null
}
