import { Sun, Moon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useThemeContext } from "./ThemeProvider";

/**
 * Button to toggle between dark and light themes
 */
export function ThemeToggle() {
  const { isDark, toggleTheme } = useThemeContext();

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggleTheme}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      className="h-7.5 w-7.5 sm:h-9 sm:w-9"
    >
      {isDark ? (
        <Sun className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
      ) : (
        <Moon className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
      )}
    </Button>
  );
}
