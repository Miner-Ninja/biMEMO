import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/utils/utils";

interface FileMentionProps {
  files: string[];
  selectedIndex: number;
  onSelect: (filename: string) => void;
  onHover: (index: number) => void;
}

/**
 * @mention autocomplete popup for file references
 */
export function FileMention({
  files,
  selectedIndex,
  onSelect,
  onHover,
}: FileMentionProps) {
  const { t } = useTranslation();
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    itemRefs.current[selectedIndex]?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  if (files.length === 0) {
    return (
      <Card className="shadow-md">
        <CardContent className="p-2 text-sm text-muted-foreground">
          {t("chat.noMatchingFiles")}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="max-h-48 overflow-y-auto shadow-md">
      <CardContent className="flex flex-col gap-1 p-1">
        {files.map((filename, index) => (
          <Button
            key={filename}
            ref={(el) => {
              itemRefs.current[index] = el;
            }}
            type="button"
            variant="ghost"
            className={cn(
              "h-auto w-full justify-start gap-2 px-3 py-1.5 text-left text-sm",
              index === selectedIndex
                ? "bg-accent text-accent-foreground"
                : "text-popover-foreground hover:bg-accent/50"
            )}
            onMouseDown={(e) => {
              e.preventDefault();
              onSelect(filename);
            }}
            onMouseEnter={() => onHover(index)}
          >
            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="truncate">{filename}</span>
          </Button>
        ))}
      </CardContent>
    </Card>
  );
}
