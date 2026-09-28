import type { Metadata } from "next";

import { LegalDocument, type LegalSection } from "@/components/legal-document";

export const metadata: Metadata = {
  title: "Privacy Policy | UMedia",
  description: "How a UMedia installation may handle account, provider, and file data.",
};

const sections: LegalSection[] = [
  {
    title: "Who operates this installation",
    paragraphs: [
      "UMedia is self-hosted software. The person or organization that deploys and administers a particular installation is responsible for deciding how that installation processes personal data, where it is hosted, and how to respond to privacy requests.",
      "If you are using someone else’s UMedia installation, contact that installation’s operator for controller identity, contact details, retention periods, and applicable local privacy information.",
    ],
  },
  {
    title: "Information we handle",
    paragraphs: [
      "Depending on the configuration of the installation, UMedia may handle the following information:",
    ],
    bullets: [
      "Account information such as an email address, optional name, role, and account status.",
      "Authentication data such as password-derived values, session cookies, and authentication events.",
      "Provider connection information, including encrypted credentials, OAuth tokens, endpoints, and connection names.",
      "File and folder data, including names, paths, MIME types, sizes, timestamps, provider identifiers, and file contents.",
      "Operational information such as error details, transfer status, synchronization status, and application logs.",
      "Preferences stored in the browser, such as theme, language, layout, and recently visited locations.",
    ],
  },
  {
    title: "Why this information is used",
    paragraphs: [
      "The operator uses this information to authenticate users, enforce permissions, connect to configured storage providers, display and transfer files, maintain the installation, troubleshoot failures, and protect the service from misuse.",
      "The operator should document the applicable legal bases, legitimate interests, and any additional processing in a version of this notice appropriate for the installation’s jurisdiction.",
    ],
  },
  {
    title: "Connected providers and sharing",
    paragraphs: [
      "When you connect a provider, UMedia sends the requests needed to authenticate and perform the operations you ask it to perform. The connected provider’s own terms and privacy policy also apply.",
      "The operator may disclose information to hosting, infrastructure, monitoring, or other service providers used by the installation, and when required by law. UMedia does not sell personal information as part of the core software.",
    ],
  },
  {
    title: "Storage and retention",
    paragraphs: [
      "The installation operator controls the database, logs, uploaded files, and connected provider accounts. Retention depends on the operator’s deployment, backup, provider, and deletion settings. Deleting an item in UMedia may not remove copies held by a connected provider or backup.",
    ],
  },
  {
    title: "Cookies and browser storage",
    paragraphs: [
      "UMedia uses essential session cookies for authentication. It may also store interface preferences in browser storage. These settings are not used for advertising or cross-site tracking by the core application.",
    ],
  },
  {
    title: "Security",
    paragraphs: [
      "UMedia is designed to keep provider configuration encrypted at rest, limit provider access through authenticated connections, and use protected session cookies. No software or transmission method is completely secure. Operators remain responsible for updates, secrets, backups, network configuration, access control, and the security of the host and connected providers.",
    ],
  },
  {
    title: "Your choices and privacy requests",
    paragraphs: [
      "Depending on the applicable law, you may have rights to access, correct, export, restrict, or delete personal information, or to object to certain processing. Send requests to the operator of the installation. The operator should verify the requester’s identity and respond within the period required by applicable law.",
      "This page is a product template, not a determination of the legal obligations of a particular operator. Before public deployment, the operator should replace or supplement it with their legal name, contact details, retention policy, jurisdiction-specific rights, and complaint route.",
    ],
  },
];

export default function PrivacyPolicyPage() {
  return (
    <LegalDocument
      currentPage="privacy"
      description="A plain-language overview of the data a self-hosted UMedia installation can process."
      intro="Because UMedia is self-hosted, the operator of the installation—not the software alone—determines the final data practices for your instance."
      sections={sections}
      title="Privacy Policy"
    />
  );
}
