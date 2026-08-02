import { useState } from "react";
import { Brain, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

interface ThinkingBlockProps {
  thinking: string;
  className?: string;
}

/**
 * Collapsible block displaying LLM thinking/reasoning process.
 * Shows the internal reasoning that led to the response.
 */
export function ThinkingBlock({ thinking, className }: ThinkingBlockProps) {
  const [isOpen, setIsOpen] = useState(true);

  if (!thinking) return null;

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <Card className={cn("shadow-none", className)}>
        <CardContent className="p-0">
          <CollapsibleTrigger className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm text-muted-foreground">
            <ChevronRight
              className={cn("h-4 w-4 transition-transform", isOpen && "rotate-90")}
            />
            <Brain className="h-4 w-4" />
            <span className="font-medium">Thinking</span>
            <Badge variant="secondary" className="ml-auto">
              {thinking.length} chars
            </Badge>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <Separator />
            <div className="p-4">
              <pre className="whitespace-pre-wrap font-mono text-sm leading-relaxed text-muted-foreground/85">
                {thinking}
              </pre>
            </div>
          </CollapsibleContent>
        </CardContent>
      </Card>
    </Collapsible>
  );
}
