"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { useLocale } from "@/components/locale-provider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useCopy } from "@/lib/copy";

const THEMES = [
  { value: "light", icon: Sun, labelKey: "themeLight" as const },
  { value: "dark", icon: Moon, labelKey: "themeDark" as const },
  { value: "system", icon: Monitor, labelKey: "themeSystem" as const },
];

/** Inline Light / Dark / System picker for Settings (not the compact
 * header dropdown). Same three values `next-themes` already persists. */
export function ThemePicker() {
  const { theme, setTheme } = useTheme();
  const { locale } = useLocale();
  const text = useCopy(locale);
  const [mounted, setMounted] = useState(false);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return <div className="h-9 w-full max-w-md rounded-lg bg-muted" />;
  }

  const current = theme ?? "system";

  return (
    <div
      aria-label={text.themeToggle}
      className="flex w-full max-w-md rounded-lg border p-1"
      role="radiogroup"
    >
      {THEMES.map(({ value, icon: Icon, labelKey }) => {
        const selected = current === value;
        return (
          <Button
            aria-checked={selected}
            className={cn(
              "flex-1 gap-2",
              selected
                ? "bg-background shadow-sm"
                : "bg-transparent text-muted-foreground hover:text-foreground",
            )}
            key={value}
            onClick={() => setTheme(value)}
            role="radio"
            size="sm"
            type="button"
            variant="ghost"
          >
            <Icon />
            {text[labelKey]}
          </Button>
        );
      })}
    </div>
  );
}
