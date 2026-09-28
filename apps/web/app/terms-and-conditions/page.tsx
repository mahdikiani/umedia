import type { Metadata } from "next";

import { LegalDocument, type LegalSection } from "@/components/legal-document";

export const metadata: Metadata = {
  title: "Terms and Conditions | UMedia",
  description: "Terms for using a self-hosted UMedia installation.",
};

const sections: LegalSection[] = [
  {
    title: "About these terms",
    paragraphs: [
      "These terms describe the general conditions for using a UMedia installation. For a self-hosted deployment, the operator of the installation may publish additional terms, service commitments, or an acceptable-use policy that apply to its users.",
      "By accessing an installation, you agree to use it lawfully and to follow the instructions and policies provided by its operator.",
    ],
  },
  {
    title: "Your responsibilities",
    bullets: [
      "Keep your account credentials and connected-provider credentials confidential.",
      "Only connect storage accounts and content that you are authorized to access.",
      "Comply with applicable law, the rights of other people, and the terms of every connected provider.",
      "Review sharing links, access keys, and user permissions before distributing them.",
      "Maintain current backups of important data and verify that transfers completed successfully.",
      "Tell the installation operator promptly if you discover unauthorized access or a security issue.",
    ],
  },
  {
    title: "Acceptable use",
    paragraphs: [
      "You must not use UMedia to access, store, transfer, or share unlawful content; infringe intellectual-property, privacy, or other rights; distribute malware; interfere with the installation or a connected provider; bypass authentication or access controls; or probe systems without authorization.",
      "The operator may suspend access, disable a provider connection, remove content where legally required, or preserve evidence of misuse when reasonably necessary to protect users, the installation, or third parties.",
    ],
  },
  {
    title: "Connected providers",
    paragraphs: [
      "UMedia is an interface to storage providers; it does not replace their terms, availability guarantees, permissions, retention rules, or security controls. Provider outages, rate limits, policy changes, and deletions can affect what UMedia can display or do.",
      "You authorize the installation to perform the provider operations you request through the permissions you configure. Review scopes and credentials before connecting an account.",
    ],
  },
  {
    title: "Availability and changes",
    paragraphs: [
      "An operator may update, configure, restrict, suspend, or discontinue an installation. Features and provider adapters may change, and no uninterrupted or error-free operation is promised unless the operator separately commits to it.",
      "The operator may update these terms when the installation or its data practices change. The effective date shown on this page identifies the current published version.",
    ],
  },
  {
    title: "Ownership and licenses",
    paragraphs: [
      "UMedia software is provided under the license included with the project. Your files, provider accounts, and other content remain subject to your rights and the terms that apply to them. You grant the installation only the permissions needed to operate the features you use.",
    ],
  },
  {
    title: "Disclaimers and responsibility",
    paragraphs: [
      "To the maximum extent permitted by applicable law, UMedia and its contributors are not responsible for loss caused by an operator’s deployment, insecure configuration, unavailable providers, revoked credentials, unsupported provider behavior, or failure to maintain independent backups.",
      "Nothing in these terms excludes or limits liability that cannot lawfully be excluded or limited. If the installation is operated as a service, its operator should add the governing law, venue, warranty, liability, support, and consumer-rights terms required for that service.",
    ],
  },
  {
    title: "Contact",
    paragraphs: [
      "For questions about access, content, billing, support, or these terms, contact the operator of the UMedia installation you use.",
    ],
  },
];

export default function TermsAndConditionsPage() {
  return (
    <LegalDocument
      currentPage="terms"
      description="General terms for using UMedia to connect and manage storage providers."
      intro="UMedia is self-hosted software. The operator of each installation is responsible for its service, configuration, users, and legal terms."
      sections={sections}
      title="Terms and Conditions"
    />
  );
}
