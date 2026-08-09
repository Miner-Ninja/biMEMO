import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { getTimezones } from "@/lib/timezones";

interface TimezoneComboboxProps {
  value: string;
  onChange: (value: string) => void;
}

const MAX_ITEMS = 80;

export function TimezoneCombobox({ value, onChange }: TimezoneComboboxProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const timezones = useMemo(() => getTimezones(), []);
  const filtered = useMemo(
    () =>
      timezones
        .filter((timezone) => timezone.toLowerCase().includes(value.toLowerCase()))
        .slice(0, MAX_ITEMS),
    [timezones, value]
  );

  useEffect(() => {
    function handleMouseDown(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
        setHighlighted(-1);
      }
    }

    document.addEventListener("mousedown", handleMouseDown);
    return () => document.removeEventListener("mousedown", handleMouseDown);
  }, []);

  useEffect(() => {
    if (highlighted >= 0 && listRef.current) {
      const item = listRef.current.children[highlighted] as HTMLElement | undefined;
      item?.scrollIntoView({ block: "nearest" });
    }
  }, [highlighted]);

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      setOpen(true);
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlighted((current) => Math.min(current + 1, filtered.length - 1));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlighted((current) => Math.max(current - 1, 0));
      return;
    }
    if (event.key === "Enter" && highlighted >= 0) {
      event.preventDefault();
      onChange(filtered[highlighted]);
      setOpen(false);
      setHighlighted(-1);
      return;
    }
    if (event.key === "Escape") {
      setOpen(false);
      setHighlighted(-1);
    }
  }

  const showDropdown = open && timezones.length > 0;

  return (
    <div ref={containerRef} className="relative">
      <Input
        id="timezone"
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          setOpen(true);
          setHighlighted(-1);
        }}
        onFocus={() => {
          setOpen(true);
          setHighlighted(-1);
        }}
        onKeyDown={handleKeyDown}
        placeholder={t("admin.searchTimezonePlaceholder")}
        autoComplete="off"
      />

      {showDropdown && (
        <ul
          className="absolute z-50 mt-1 max-h-60 w-full overflow-y-auto rounded-md border border-border bg-background shadow-md"
          ref={listRef}
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-sm text-muted-foreground">
              {t("admin.noTimezonesFound")}
            </li>
          ) : (
            filtered.map((timezone, index) => (
              <li
                key={timezone}
                className={`cursor-pointer px-3 py-2 text-sm ${
                  index === highlighted
                    ? "bg-accent text-accent-foreground"
                    : "hover:bg-accent hover:text-accent-foreground"
                }`}
                onMouseDown={(event) => {
                  event.preventDefault();
                  onChange(timezone);
                  setOpen(false);
                  setHighlighted(-1);
                }}
                onMouseEnter={() => setHighlighted(index)}
              >
                {timezone}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
