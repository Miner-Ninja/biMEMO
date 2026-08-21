import * as React from "react";

import { cn } from "@/utils/utils";

type SwitchProps = Omit<React.ComponentProps<"button">, "onChange"> & {
  checked: boolean;
  onCheckedChange?: (checked: boolean) => void;
};

function Switch({
  checked,
  onCheckedChange,
  className,
  disabled,
  type = "button",
  ...props
}: SwitchProps) {
  return (
    <button
      type={type}
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      data-slot="switch"
      className={cn(
        "inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-transparent transition-colors outline-none",
        "focus-visible:ring-ring/50 focus-visible:ring-[3px] focus-visible:ring-offset-2",
        "disabled:pointer-events-none disabled:opacity-50",
        checked ? "bg-primary" : "bg-muted",
        className
      )}
      onClick={() => onCheckedChange?.(!checked)}
      {...props}
    >
      <span
        className={cn(
          "block h-5 w-5 rounded-full bg-background shadow-sm transition-transform",
          checked ? "translate-x-5" : "translate-x-0"
        )}
      />
    </button>
  );
}

export { Switch };
