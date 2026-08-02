import type { ComponentPropsWithoutRef } from "react";
import { MarkdownCodeBlock } from "@/components/chat/MarkdownCodeBlock";
import { DataChart } from "@/components/chat/DataChart";
import type { ChartData } from "@/lib/chart-utils";

function extractCodeBlockContent(
  children: ComponentPropsWithoutRef<"pre">["children"]
) {
  const firstChild = Array.isArray(children) ? children[0] : children;

  if (firstChild && typeof firstChild === "object" && "props" in firstChild) {
    return {
      code: String(firstChild.props.children ?? "").replace(/\n$/, ""),
      language:
        String(firstChild.props?.className || "").replace(/^language-/, "") ||
        undefined,
    };
  }

  if (typeof children === "string") {
    return {
      code: children.replace(/\n$/, ""),
      language: undefined,
    };
  }

  if (Array.isArray(children)) {
    const combinedCode = children
      .map((child) => {
        if (typeof child === "string") {
          return child;
        }
        if (child && typeof child === "object" && "props" in child) {
          return String(child.props.children ?? "");
        }
        return "";
      })
      .join("")
      .replace(/\n$/, "");

    if (combinedCode) {
      return {
        code: combinedCode,
        language: undefined,
      };
    }
  }

  return null;
}

export function buildMarkdownComponents(onCommandClick?: (command: string) => void) {
  return {
    a: ({ href, children, ...props }: ComponentPropsWithoutRef<"a">) => (
      <a {...props} href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    ),
    pre: ({ children, ...props }: ComponentPropsWithoutRef<"pre">) => {
      const extractedCodeBlock = extractCodeBlockContent(children);

      if (extractedCodeBlock) {
        const { code, language } = extractedCodeBlock;

        if (language === "chart") {
          try {
            const chartData: ChartData = JSON.parse(code.trim());
            return <DataChart chart={chartData} />;
          } catch {
            // Fall through to default rendering if JSON is invalid
          }
        }

        return <MarkdownCodeBlock code={code} language={language} />;
      }

      return <pre {...props}>{children}</pre>;
    },
    code: ({
      children,
      className,
      ...props
    }: ComponentPropsWithoutRef<"code"> & { className?: string }) => {
      if (className === "language-chart") {
        return (
          <code className={className} {...props}>
            {children}
          </code>
        );
      }
      const text = String(children).trim();
      if (onCommandClick && text.startsWith("/")) {
        const baseCommand = text.split(/\s/)[0];
        return (
          <code
            {...props}
            role="button"
            tabIndex={0}
            className="cursor-pointer rounded bg-primary/10 px-1.5 py-0.5 text-primary hover:bg-primary/20 transition-colors"
            onClick={() => onCommandClick(baseCommand)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") onCommandClick(baseCommand);
            }}
          >
            {children}
          </code>
        );
      }
      return (
        <code className={className} {...props}>
          {children}
        </code>
      );
    },
  };
}
