import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/core";
import pythonLanguage from "highlight.js/lib/languages/python";
import powershellLanguage from "highlight.js/lib/languages/powershell";
import shellLanguage from "highlight.js/lib/languages/bash";
import sqlLanguage from "highlight.js/lib/languages/sql";
import jsonLanguage from "highlight.js/lib/languages/json";
import yamlLanguage from "highlight.js/lib/languages/yaml";
import typescriptLanguage from "highlight.js/lib/languages/typescript";
import xmlLanguage from "highlight.js/lib/languages/xml";
import cssLanguage from "highlight.js/lib/languages/css";
import plaintextLanguage from "highlight.js/lib/languages/plaintext";
import "highlight.js/styles/atom-one-dark.css";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface MarkdownCodeBlockProps {
  code: string;
  language?: string | undefined;
  className?: string;
}

const FALLBACK_LANGUAGE = "plaintext";
const ALLOWED_ATTRIBUTES = ["class"];
const LANGUAGE_ALIASES: Record<string, string> = {
  bash: "bash",
  css: "css",
  html: "xml",
  js: "typescript",
  json: "json",
  powershell: "powershell",
  ps1: "powershell",
  py: "python",
  python: "python",
  shell: "bash",
  sh: "bash",
  sql: "sql",
  text: "plaintext",
  ts: "typescript",
  typescript: "typescript",
  xml: "xml",
  yaml: "yaml",
  yml: "yaml",
};

hljs.registerLanguage("bash", shellLanguage);
hljs.registerLanguage("css", cssLanguage);
hljs.registerLanguage("json", jsonLanguage);
hljs.registerLanguage("plaintext", plaintextLanguage);
hljs.registerLanguage("powershell", powershellLanguage);
hljs.registerLanguage("python", pythonLanguage);
hljs.registerLanguage("sql", sqlLanguage);
hljs.registerLanguage("typescript", typescriptLanguage);
hljs.registerLanguage("xml", xmlLanguage);
hljs.registerLanguage("yaml", yamlLanguage);

function isLikelyJson(code: string): boolean {
  const trimmedCode = code.trim();
  if (!(trimmedCode.startsWith("{") || trimmedCode.startsWith("["))) {
    return false;
  }

  try {
    JSON.parse(trimmedCode);
    return true;
  } catch {
    return false;
  }
}

function inferCodeLanguage(code: string): string {
  const trimmedCode = code.trim();

  if (!trimmedCode) {
    return FALLBACK_LANGUAGE;
  }

  if (/^#!.*\b(bash|sh|zsh)\b/.test(trimmedCode)) {
    return "bash";
  }

  if (isLikelyJson(trimmedCode)) {
    return "json";
  }

  if (/<[a-z][\s\S]*>/i.test(trimmedCode)) {
    return "xml";
  }

  if (
    /^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP)\b/im.test(trimmedCode)
  ) {
    return "sql";
  }

  if (
    /\b(Get|Set|New|Remove|Select|Where|ForEach)-[A-Z]/.test(trimmedCode) ||
    /\$\w+/.test(trimmedCode)
  ) {
    return "powershell";
  }

  if (
    /^\s*(def|class|import|from)\b/m.test(trimmedCode) ||
    /if __name__ == ['"]__main__['"]/.test(trimmedCode) ||
    /\bprint\(/.test(trimmedCode)
  ) {
    return "python";
  }

  if (
    /^\s*(const|let|var|function|import|export)\b/m.test(trimmedCode) ||
    /=>/.test(trimmedCode) ||
    /console\.log\(/.test(trimmedCode)
  ) {
    return "typescript";
  }

  if (
    /^\s*[a-zA-Z-]+\s*:\s*[^;]+;\s*$/m.test(trimmedCode) &&
    trimmedCode.includes("{") &&
    trimmedCode.includes("}")
  ) {
    return "css";
  }

  if (/^\s*[a-zA-Z0-9_-]+\s*:\s*.+$/m.test(trimmedCode) && !trimmedCode.includes("{")) {
    return "yaml";
  }

  if (
    /\b(echo|export|grep|sed|awk|curl|chmod|chown|sudo)\b/.test(trimmedCode) ||
    /(^|\n)\s*\w+=["']?.+["']?\s*$/m.test(trimmedCode)
  ) {
    return "bash";
  }

  return FALLBACK_LANGUAGE;
}

function resolveLanguage(code: string, language?: string): string {
  if (!language) {
    return inferCodeLanguage(code);
  }

  const normalizedLanguage = language.toLowerCase();
  return LANGUAGE_ALIASES[normalizedLanguage] ?? inferCodeLanguage(code);
}

/**
 * Highlighted markdown code block with copy button.
 */
export function MarkdownCodeBlock({
  code,
  language,
  className,
}: MarkdownCodeBlockProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const html = useMemo(() => {
    const resolvedLanguage = resolveLanguage(code, language);

    try {
      const highlightedCode = hljs.highlight(code, {
        language: resolvedLanguage,
        ignoreIllegals: true,
      }).value;
      return DOMPurify.sanitize(highlightedCode, {
        ALLOWED_ATTR: ALLOWED_ATTRIBUTES,
      });
    } catch {
      return DOMPurify.sanitize(
        hljs.highlight(code, {
          language: FALLBACK_LANGUAGE,
          ignoreIllegals: true,
        }).value,
        {
          ALLOWED_ATTR: ALLOWED_ATTRIBUTES,
        }
      );
    }
  }, [code, language]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error("Failed to copy:", error);
    }
  };

  return (
    <Card
      className={cn(
        "not-prose group my-3 overflow-hidden bg-card/80 shadow-none",
        className
      )}
    >
      <CardContent className="p-0">
        <div className="flex items-center justify-between border-b border-border/70 px-4 py-2">
          <Badge variant="secondary">{resolveLanguage(code, language)}</Badge>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleCopy}
            className="h-8 w-8"
            aria-label={copied ? t("chat.copied") : t("chat.copyCode")}
          >
            {copied ? (
              <Check className="h-4 w-4 text-primary" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
          </Button>
        </div>
        <pre className="hljs overflow-x-auto border-0 !bg-sidebar p-4 text-base text-sidebar-foreground">
          <code
            className="font-mono text-inherit"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        </pre>
      </CardContent>
    </Card>
  );
}
