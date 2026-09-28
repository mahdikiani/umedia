import Link from "next/link";

import { PublicLegalFooter } from "@/components/public-legal-footer";

export type LegalSection = {
  title: string;
  paragraphs?: string[];
  bullets?: string[];
};

type LegalDocumentProps = {
  currentPage: "privacy" | "terms";
  description: string;
  intro: string;
  sections: LegalSection[];
  title: string;
};

export function LegalDocument({
  currentPage,
  description,
  intro,
  sections,
  title,
}: LegalDocumentProps) {
  return (
    <main className="min-h-screen bg-muted/30 px-4 py-8 text-foreground sm:py-12">
      <div className="mx-auto max-w-3xl">
        <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <Link className="text-sm font-semibold tracking-tight hover:underline" href="/login" prefetch={false}>
            UMedia
          </Link>
          <nav aria-label="Legal" className="flex items-center gap-4 text-sm text-muted-foreground">
            <Link
              className={currentPage === "privacy" ? "font-medium text-foreground" : "hover:text-foreground"}
              href="/privacy-policy"
              prefetch={false}
            >
              Privacy Policy
            </Link>
            <Link
              className={currentPage === "terms" ? "font-medium text-foreground" : "hover:text-foreground"}
              href="/terms-and-conditions"
              prefetch={false}
            >
              Terms
            </Link>
          </nav>
        </header>

        <article className="rounded-xl border bg-card p-6 sm:p-10">
          <header className="border-b pb-6">
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
              UMedia legal information
            </p>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight">{title}</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">{description}</p>
            <p className="mt-4 text-sm leading-6">{intro}</p>
            <p className="mt-4 text-xs text-muted-foreground">Last updated: September 27, 2026</p>
          </header>

          <div className="divide-y">
            {sections.map((section) => (
              <section className="space-y-3 py-6 first:pt-8 last:pb-2" key={section.title}>
                <h2 className="text-lg font-semibold tracking-tight">{section.title}</h2>
                {section.paragraphs?.map((paragraph) => (
                  <p className="text-sm leading-6 text-muted-foreground" key={paragraph}>
                    {paragraph}
                  </p>
                ))}
                {section.bullets && (
                  <ul className="list-disc space-y-2 ps-5 text-sm leading-6 text-muted-foreground">
                    {section.bullets.map((bullet) => (
                      <li key={bullet}>{bullet}</li>
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>
        </article>

        <PublicLegalFooter />
      </div>
    </main>
  );
}
