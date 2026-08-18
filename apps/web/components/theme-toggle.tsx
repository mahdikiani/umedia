"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { useLocale } from "@/components/locale-provider";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useCopy } from "@/lib/copy";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const { locale } = useLocale();
  const text = useCopy(locale);
  // next-themes only knows the real theme after mount (it reads
  // localStorage/media-query client-side); rendering before that would
  // flash the wrong icon / wrong radio value.
  const [mounted, setMounted] = useState(false);
  // The standard next-themes "avoid a hydration flash" mount flag -- an
  // intentional one-time client/server divergence, not a cascading update.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setMounted(true), []);

  if (!mounted) return <div className="size-8" />;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button aria-label={text.themeToggle} size="icon" variant="ghost" />}
      >
        <Sun className="dark:hidden" />
        <Moon className="hidden dark:block" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuGroup>
          <DropdownMenuRadioGroup
            value={theme ?? "system"}
            onValueChange={(value) => {
              if (value) setTheme(value);
            }}
          >
            <DropdownMenuRadioItem value="light">
              <Sun />
              {text.themeLight}
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="dark">
              <Moon />
              {text.themeDark}
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="system">
              <Monitor />
              {text.themeSystem}
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
