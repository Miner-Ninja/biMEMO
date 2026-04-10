/**
 * Extract SQL from markdown code blocks in content.
 * Returns { sql, contentWithoutSql }
 */
export function extractSqlFromContent(content: string): { sql: string | null; contentWithoutSql: string } {
  // Match ```sql ... ``` blocks
  const sqlBlockRegex = /```sql\s*([\s\S]*?)```/gi
  const matches = content.match(sqlBlockRegex)

  if (!matches || matches.length === 0) {
    return { sql: null, contentWithoutSql: content }
  }

  // Extract SQL from first match (remove ```sql and ```)
  const sqlMatch = matches[0]
  const sql = sqlMatch.replace(/```sql\s*/i, '').replace(/```$/, '').trim()

  // Remove all SQL blocks from content
  const contentWithoutSql = content.replace(sqlBlockRegex, '').trim()

  return { sql, contentWithoutSql }
}

function detectScriptProfile(text: string): "latin" | "cyrillic" | "mixed" | "neutral" {
  const latinMatches = text.match(/[A-Za-z]/g) ?? []
  const cyrillicMatches = text.match(/[А-Яа-яЁё]/g) ?? []
  const latinCount = latinMatches.length
  const cyrillicCount = cyrillicMatches.length

  if (latinCount === 0 && cyrillicCount === 0) {
    return "neutral"
  }
  if (latinCount > 0 && cyrillicCount === 0) {
    return "latin"
  }
  if (cyrillicCount > 0 && latinCount === 0) {
    return "cyrillic"
  }

  const dominantCount = Math.max(latinCount, cyrillicCount)
  const totalCount = latinCount + cyrillicCount
  if (dominantCount / totalCount >= 0.8) {
    return latinCount > cyrillicCount ? "latin" : "cyrillic"
  }
  return "mixed"
}

function suggestionsMatchContentLanguage(
  cleanContent: string,
  suggestions: string[],
): boolean {
  const contentProfile = detectScriptProfile(cleanContent)
  if (contentProfile === "neutral" || contentProfile === "mixed") {
    return true
  }

  const suggestionProfile = detectScriptProfile(suggestions.join(" "))
  if (suggestionProfile === "neutral" || suggestionProfile === "mixed") {
    return true
  }

  return contentProfile === suggestionProfile
}

/**
 * Extract suggestions from ```suggestions code blocks in content.
 */
export function extractSuggestions(
  contentWithoutSql: string,
  isUser: boolean,
): { cleanContent: string; suggestions: string[] } {
  if (!contentWithoutSql || isUser) {
    return { cleanContent: contentWithoutSql, suggestions: [] }
  }

  // Step 1: Extract ```suggestions blocks
  const suggestionsFound: string[] = []
  let cleaned = contentWithoutSql.replace(
    /```suggestions\s*\n([\s\S]*?)```/g,
    (_match, block: string) => {
      const lines = block.split('\n').map((l: string) => l.trim()).filter(Boolean)
      suggestionsFound.push(...lines)
      return ''
    }
  )

  if (suggestionsFound.length > 0) {
    const trimmedContent = cleaned.trim()
    const trimmedSuggestions = suggestionsFound.slice(0, 3)
    return {
      cleanContent: trimmedContent,
      suggestions: suggestionsMatchContentLanguage(
        trimmedContent,
        trimmedSuggestions,
      )
        ? trimmedSuggestions
        : [],
    }
  }

  return { cleanContent: cleaned.trim(), suggestions: [] }
}
