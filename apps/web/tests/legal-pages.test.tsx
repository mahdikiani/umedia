import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import PrivacyPolicyPage, {
  metadata as privacyMetadata,
} from "@/app/privacy-policy/page";
import TermsAndConditionsPage, {
  metadata as termsMetadata,
} from "@/app/terms-and-conditions/page";

afterEach(cleanup);

function hasHref(links: HTMLElement[], href: string): boolean {
  return links.some((link) => link.getAttribute("href") === href);
}

describe("legal pages", () => {
  it("renders the privacy policy with operator and data-handling guidance", () => {
    render(<PrivacyPolicyPage />);

    expect(
      screen.getByRole("heading", { name: "Privacy Policy" }),
    ).toBeTruthy();
    expect(screen.getByText(/self-hosted UMedia installation/i)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Information we handle" })).toBeTruthy();
    expect(
      hasHref(screen.getAllByRole("link", { name: "Terms and Conditions" }), "/terms-and-conditions"),
    ).toBe(true);
  });

  it("renders the terms with acceptable-use and responsibility sections", () => {
    render(<TermsAndConditionsPage />);

    expect(
      screen.getByRole("heading", { name: "Terms and Conditions" }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Your responsibilities" })).toBeTruthy();
    expect(screen.getAllByText(/operator of the installation/i).length).toBeGreaterThan(0);
    expect(
      hasHref(screen.getAllByRole("link", { name: "Privacy Policy" }), "/privacy-policy"),
    ).toBe(true);
  });

  it("exposes route-specific metadata", () => {
    expect(privacyMetadata.title).toBe("Privacy Policy | UMedia");
    expect(termsMetadata.title).toBe("Terms and Conditions | UMedia");
  });
});
