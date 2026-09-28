import Link from "next/link";

export function PublicLegalFooter() {
  return (
    <footer className="mt-8 flex flex-wrap items-center justify-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
      <span>UMedia</span>
      <Link className="underline-offset-4 hover:text-foreground hover:underline" href="/privacy-policy" prefetch={false}>
        Privacy Policy
      </Link>
      <Link className="underline-offset-4 hover:text-foreground hover:underline" href="/terms-and-conditions" prefetch={false}>
        Terms and Conditions
      </Link>
    </footer>
  );
}
