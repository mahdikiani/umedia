"use client";

import { useLocale, type Locale } from "@/components/locale-provider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const LABELS: Record<Locale, { flag: string; short: string; long: string }> = {
  en: { flag: "🇬🇧", short: "EN", long: "English" },
  fa: { flag: "🇮🇷", short: "FA", long: "فارسی" },
};

type LocaleToggleProps = {
  /** Compact header control vs login/onboarding label. */
  label?: "short" | "long";
  className?: string;
  variant?: "ghost" | "outline";
  size?: "sm" | "default";
};

/** Language switcher — same font stack as the rest of the chrome (`font-sans`),
 * not the heavier default Button `font-medium`, so it matches sidebar items. */
export function LocaleToggle({
  label = "short",
  className,
  variant = "ghost",
  size = "sm",
}: LocaleToggleProps) {
  const { locale, setLocale } = useLocale();
  const current = LABELS[locale];

  return (
    <Button
      className={cn("font-sans font-normal", className)}
      onClick={() => setLocale(locale === "en" ? "fa" : "en")}
      size={size}
      type="button"
      variant={variant}
    >
      <span aria-hidden className="text-base leading-none">
        {current.flag}
      </span>
      {label === "short" ? current.short : current.long}
    </Button>
  );
}
