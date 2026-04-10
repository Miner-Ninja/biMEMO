import { useMemo, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/core'
import pythonLanguage from 'highlight.js/lib/languages/python'
import powershellLanguage from 'highlight.js/lib/languages/powershell'
import shellLanguage from 'highlight.js/lib/languages/bash'
import sqlLanguage from 'highlight.js/lib/languages/sql'
import jsonLanguage from 'highlight.js/lib/languages/json'
import yamlLanguage from 'highlight.js/lib/languages/yaml'
import typescriptLanguage from 'highlight.js/lib/languages/typescript'
import xmlLanguage from 'highlight.js/lib/languages/xml'
import cssLanguage from 'highlight.js/lib/languages/css'
import plaintextLanguage from 'highlight.js/lib/languages/plaintext'
import 'highlight.js/styles/atom-one-dark.css'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'


interface MarkdownCodeBlockProps {
  code: string
  language?: string
  className?: string
}


const FALLBACK_LANGUAGE = 'plaintext'
const ALLOWED_ATTRIBUTES = ['class']
const LANGUAGE_ALIASES: Record<string, string> = {
  bash: 'bash',
  css: 'css',
  html: 'xml',
  js: 'typescript',
  json: 'json',
  powershell: 'powershell',
  ps1: 'powershell',
  py: 'python',
  python: 'python',
  shell: 'bash',
  sh: 'bash',
  sql: 'sql',
  text: 'plaintext',
  ts: 'typescript',
  typescript: 'typescript',
  xml: 'xml',
  yaml: 'yaml',
  yml: 'yaml',
}

hljs.registerLanguage('bash', shellLanguage)
hljs.registerLanguage('css', cssLanguage)
hljs.registerLanguage('json', jsonLanguage)
hljs.registerLanguage('plaintext', plaintextLanguage)
hljs.registerLanguage('powershell', powershellLanguage)
hljs.registerLanguage('python', pythonLanguage)
hljs.registerLanguage('sql', sqlLanguage)
hljs.registerLanguage('typescript', typescriptLanguage)
hljs.registerLanguage('xml', xmlLanguage)
hljs.registerLanguage('yaml', yamlLanguage)


function isLikelyJson(code: string): boolean {
  const trimmedCode = code.trim()
  if (!(trimmedCode.startsWith('{') || trimmedCode.startsWith('['))) {
    return false
  }

  try {
    JSON.parse(trimmedCode)
    return true
  } catch {
    return false
  }
}


function inferCodeLanguage(code: string): string {
  const trimmedCode = code.trim()

  if (!trimmedCode) {
    return FALLBACK_LANGUAGE
  }

  if (/^#!.*\b(bash|sh|zsh)\b/.test(trimmedCode)) {
    return 'bash'
  }

  if (isLikelyJson(trimmedCode)) {
    return 'json'
  }

  if (/<[a-z][\s\S]*>/i.test(trimmedCode)) {
    return 'xml'
  }

  if (
    /^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP)\b/im.test(trimmedCode)
  ) {
    return 'sql'
  }

  if (
    /\b(Get|Set|New|Remove|Select|Where|ForEach)-[A-Z]/.test(trimmedCode) ||
    /\$\w+/.test(trimmedCode)
  ) {
    return 'powershell'
  }

  if (
    /^\s*(def|class|import|from)\b/m.test(trimmedCode) ||
    /if __name__ == ['"]__main__['"]/.test(trimmedCode) ||
    /\bprint\(/.test(trimmedCode)
  ) {
    return 'python'
  }

  if (
    /^\s*(const|let|var|function|import|export)\b/m.test(trimmedCode) ||
    /=>/.test(trimmedCode) ||
    /console\.log\(/.test(trimmedCode)
  ) {
    return 'typescript'
  }

  if (
    /^\s*[a-zA-Z-]+\s*:\s*[^;]+;\s*$/m.test(trimmedCode) &&
    trimmedCode.includes('{') &&
    trimmedCode.includes('}')
  ) {
    return 'css'
  }

  if (/^\s*[a-zA-Z0-9_-]+\s*:\s*.+$/m.test(trimmedCode) && !trimmedCode.includes('{')) {
    return 'yaml'
  }

  if (
    /\b(echo|export|grep|sed|awk|curl|chmod|chown|sudo)\b/.test(trimmedCode) ||
    /(^|\n)\s*\w+=["']?.+["']?\s*$/m.test(trimmedCode)
  ) {
    return 'bash'
  }

  return FALLBACK_LANGUAGE
}


function resolveLanguage(code: string, language?: string): string {
  if (!language) {
    return inferCodeLanguage(code)
  }

  const normalizedLanguage = language.toLowerCase()
  return LANGUAGE_ALIASES[normalizedLanguage] ?? inferCodeLanguage(code)
}


/**
 * Highlighted markdown code block with copy button.
 */
export function MarkdownCodeBlock({
  code,
  language,
  className,
}: MarkdownCodeBlockProps) {
  const [copied, setCopied] = useState(false)

  const html = useMemo(() => {
    const resolvedLanguage = resolveLanguage(code, language)

    try {
      const highlightedCode = hljs.highlight(code, {
        language: resolvedLanguage,
        ignoreIllegals: true,
      }).value
      return DOMPurify.sanitize(highlightedCode, {
        ALLOWED_ATTR: ALLOWED_ATTRIBUTES,
      })
    } catch {
      return DOMPurify.sanitize(hljs.highlight(code, {
        language: FALLBACK_LANGUAGE,
        ignoreIllegals: true,
      }).value, {
        ALLOWED_ATTR: ALLOWED_ATTRIBUTES,
      })
    }
  }, [code, language])

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (error) {
      console.error('Failed to copy:', error)
    }
  }

  return (
    <div className={cn('not-prose relative group my-3', className)}>
      <div className="absolute right-2 top-2 z-10 opacity-0 transition-opacity group-hover:opacity-100">
        <Button
          variant="ghost"
          size="icon"
          onClick={handleCopy}
          className="h-8 w-8 bg-black/10 text-white hover:bg-black/20 hover:text-white dark:bg-white/10 dark:text-white dark:hover:bg-white/20"
          aria-label={copied ? 'Copied!' : 'Copy code'}
        >
          {copied ? (
            <Check className="h-4 w-4 text-green-400" />
          ) : (
            <Copy className="h-4 w-4" />
          )}
        </Button>
      </div>
      <pre className="hljs overflow-x-auto rounded-lg border border-sidebar-border !bg-sidebar p-4 text-base shadow-xs text-sidebar-foreground">
        <code
          className="font-mono text-inherit"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </pre>
    </div>
  )
}
