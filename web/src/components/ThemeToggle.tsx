import { Moon, Sun } from "lucide-react";
import { Button } from "./Button";
import { useTheme } from "@/lib/theme";

export function ThemeToggle() {
  const [theme, toggle] = useTheme();
  return (
    <Button variant="ghost" icon onClick={toggle} aria-label="Toggle theme" title="Toggle theme">
      {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
    </Button>
  );
}
