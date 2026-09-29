import type { Metadata } from "next";
import Link from "next/link";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  Cloud,
  Code2,
  Database,
  FileImage,
  FileText,
  FolderClosed,
  HardDrive,
  MoveRight,
  ShieldCheck,
} from "lucide-react";

import styles from "./landing.module.css";

const docsUrl = "https://mahdikiani.github.io/umedia/";

export const metadata: Metadata = {
  title: "UMedia — Your files. One library. Your server.",
  description:
    "A self-hosted media library for local folders, S3-compatible storage, and selected cloud providers. Open source and built to run on your infrastructure.",
  alternates: { canonical: "https://umedia.uln.me/" },
  openGraph: {
    title: "UMedia — Your files. One library. Your server.",
    description:
      "Bring files from different storage providers into one self-hosted library.",
    url: "https://umedia.uln.me/",
    siteName: "UMedia",
    type: "website",
  },
};

function BrandMark() {
  return (
    <span className={styles.brandMark} aria-hidden="true">
      <FolderClosed size={19} strokeWidth={1.8} />
    </span>
  );
}

function ActionLink({
  children,
  href,
  primary = false,
}: {
  children: React.ReactNode;
  href: string;
  primary?: boolean;
}) {
  return (
    <Link
      className={`${styles.actionLink} ${primary ? styles.primaryAction : styles.secondaryAction}`}
      href={href}
    >
      <span>{children}</span>
      <span className={styles.actionIcon} aria-hidden="true">
        <ArrowUpRight size={16} strokeWidth={1.8} />
      </span>
    </Link>
  );
}

function LibraryPreview() {
  return (
    <figure className={styles.previewFigure}>
      <div className={styles.previewGlow} aria-hidden="true" />
      <svg className={styles.connectionLines} viewBox="0 0 660 460" fill="none" aria-hidden="true">
        <path d="M88 86C208 16 375 23 530 107" />
        <path d="M113 390C242 444 418 429 557 346" />
        <circle cx="88" cy="86" r="4" />
        <circle cx="530" cy="107" r="4" />
        <circle cx="113" cy="390" r="4" />
        <circle cx="557" cy="346" r="4" />
      </svg>
      <div className={styles.previewShell}>
        <div className={styles.previewSurface}>
          <div className={styles.windowBar}>
            <div className={styles.windowBrand}>
              <BrandMark />
              <span>UMedia</span>
            </div>
            <span className={styles.windowContext}>Personal library</span>
            <span className={styles.windowAvatar} aria-hidden="true">M</span>
          </div>
          <div className={styles.libraryLayout}>
            <aside className={styles.librarySidebar} aria-label="Illustrative library navigation">
              <span className={`${styles.sideItem} ${styles.sideItemActive}`}>
                <FolderClosed size={15} /> All files
              </span>
              <span className={styles.sideItem}><FileImage size={15} /> Photos</span>
              <span className={styles.sideItem}><FileText size={15} /> Documents</span>
              <div className={styles.sideDivider} />
              <span className={styles.sideLabel}>STORAGE</span>
              <span className={styles.storageItem}><i className={styles.localDot} /> Local</span>
              <span className={styles.storageItem}><i className={styles.s3Dot} /> S3 storage</span>
              <span className={styles.storageItem}><i className={styles.cloudDot} /> Cloud drive</span>
            </aside>
            <div className={styles.libraryContent}>
              <div className={styles.libraryHeading}>
                <div>
                  <span className={styles.libraryEyebrow}>YOUR CONTENT</span>
                  <h2>All files</h2>
                </div>
                <span className={styles.viewMenu} aria-hidden="true">•••</span>
              </div>
              <div className={styles.fileHeader}>
                <span>Name</span><span>Location</span><span>Updated</span>
              </div>
              <div className={styles.fileRow}>
                <span className={`${styles.fileIcon} ${styles.fileIconMint}`}><FileImage size={17} /></span>
                <span className={styles.fileName}>field-notes.png</span>
                <span className={styles.fileLocation}><i className={styles.localDot} /> Local</span>
                <span className={styles.fileDate}>Today</span>
              </div>
              <div className={styles.fileRow}>
                <span className={`${styles.fileIcon} ${styles.fileIconBlue}`}><FileText size={17} /></span>
                <span className={styles.fileName}>project-brief.pdf</span>
                <span className={styles.fileLocation}><i className={styles.s3Dot} /> S3</span>
                <span className={styles.fileDate}>Yesterday</span>
              </div>
              <div className={styles.fileRow}>
                <span className={`${styles.fileIcon} ${styles.fileIconLilac}`}><FileImage size={17} /></span>
                <span className={styles.fileName}>studio-frame.jpg</span>
                <span className={styles.fileLocation}><i className={styles.cloudDot} /> Drive</span>
                <span className={styles.fileDate}>May 18</span>
              </div>
              <div className={styles.libraryFoot}>
                <span><Check size={13} /> Library view</span>
                <span>Illustrative preview</span>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className={`${styles.floatCard} ${styles.floatCardTop}`}>
        <span className={styles.floatIcon}><Database size={17} /></span>
        <span><strong>Your storage</strong><small>Stays yours</small></span>
        <Check className={styles.floatCheck} size={15} />
      </div>
      <div className={`${styles.floatCard} ${styles.floatCardBottom}`}>
        <span className={styles.floatIconCloud}><Cloud size={17} /></span>
        <span><strong>One library</strong><small>Multiple providers</small></span>
        <MoveRight className={styles.floatArrow} size={17} />
      </div>
      <figcaption className="sr-only">
        Illustrative UMedia library showing files from local and S3-compatible storage in one view.
      </figcaption>
    </figure>
  );
}

const providers = [
  { name: "Local storage", status: "Available", icon: HardDrive, tone: "local" },
  { name: "S3-compatible", status: "Available", icon: Database, tone: "s3" },
  { name: "Google Drive", status: "Beta", icon: Cloud, tone: "drive" },
  { name: "OneDrive", status: "Beta", icon: Cloud, tone: "onedrive" },
  { name: "Dropbox", status: "Beta", icon: Cloud, tone: "dropbox" },
  { name: "Telegram", status: "Beta", icon: Cloud, tone: "telegram" },
] as const;

export default function Home() {
  return (
    <div className={styles.page}>
      <div className={styles.heroAtmosphere} aria-hidden="true" />
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <Link className={styles.brand} href="/" aria-label="UMedia home">
            <BrandMark />
            <span>UMedia</span>
          </Link>
          <nav className={styles.navLinks} aria-label="Main navigation">
            <a href="#features">Features</a>
            <a href="#providers">Providers</a>
            <a href={docsUrl}>Docs</a>
            <a href="https://github.com/mahdikiani/umedia">GitHub</a>
          </nav>
          <Link className={styles.headerAction} href="/login">
            Open UMedia <ArrowUpRight size={15} />
          </Link>
        </div>
      </header>

      <main>
        <section className={styles.hero} aria-labelledby="hero-title">
          <div className={styles.heroCopy}>
            <div className={styles.eyebrow}>
              <span className={styles.eyebrowDot} /> OPEN-SOURCE MEDIA LIBRARY
            </div>
            <h1 id="hero-title">Your files.<br /><span>One library.</span><br />Your server.</h1>
            <p className={styles.heroDescription}>
              Bring local folders, S3-compatible storage, and selected cloud providers into one library you run and control.
            </p>
            <div className={styles.heroActions}>
              <ActionLink href="/login" primary>Open the preview</ActionLink>
              <ActionLink href="https://github.com/mahdikiani/umedia">Explore the source</ActionLink>
            </div>
            <p className={styles.previewNote}>
              <ShieldCheck size={15} strokeWidth={1.8} /> Self-hosted by design. The hosted instance is a maintainer preview.
            </p>
          </div>
          <LibraryPreview />
        </section>

        <section className={styles.promiseStrip} aria-label="Project details">
          <span><i className={styles.stripDot} /> Open source under MIT</span>
          <span><i className={styles.stripDot} /> Runs on your infrastructure</span>
          <span><i className={styles.stripDot} /> Docker Compose + SQLite</span>
        </section>

        <section className={styles.featuresSection} id="features" aria-labelledby="features-title">
          <div className={styles.sectionHeading}>
            <span className={styles.sectionEyebrow}>A LIBRARY THAT FOLLOWS YOUR STORAGE</span>
            <h2 id="features-title">Keep your files<br />where they belong.</h2>
            <p>UMedia brings organization to the storage you already use. Your files stay with their providers while one library gives you a place to browse and manage them.</p>
          </div>
          <div className={styles.featureGrid}>
            <article className={`${styles.featureCard} ${styles.featureCardWide}`}>
              <div className={styles.cardIllustration} aria-hidden="true">
                <span className={`${styles.storageTile} ${styles.storageTileLocal}`}><HardDrive size={20} /><small>LOCAL</small></span>
                <span className={styles.transferLine}><ArrowRight size={18} /></span>
                <span className={`${styles.storageTile} ${styles.storageTileS3}`}><Database size={20} /><small>S3</small></span>
                <span className={`${styles.storageTile} ${styles.storageTileLibrary}`}><FolderClosed size={21} /><small>LIBRARY</small></span>
              </div>
              <span className={styles.cardIndex}>01 / ORGANIZE</span>
              <h3>One view across providers</h3>
              <p>Browse and organize a shared library that can reference files from different connected storage providers.</p>
            </article>
            <article className={styles.featureCard}>
              <div className={`${styles.featureIcon} ${styles.featureIconFiles}`} aria-hidden="true"><MoveRight size={20} /></div>
              <span className={styles.cardIndex}>02 / MOVE</span>
              <h3>Move at your pace</h3>
              <p>Copy or move files between providers, depending on the capabilities of each connection.</p>
            </article>
            <article className={`${styles.featureCard} ${styles.featureCardDark}`}>
              <div className={`${styles.featureIcon} ${styles.featureIconControl}`} aria-hidden="true"><ShieldCheck size={20} /></div>
              <span className={styles.cardIndex}>03 / SELF-HOST</span>
              <h3>Your infrastructure.<br />Your control.</h3>
              <p>Run UMedia yourself with Docker Compose, SQLite, and persistent storage you manage.</p>
              <a className={styles.cardLink} href={`${docsUrl}getting-started/`}>
                Read the self-hosting guide <ArrowUpRight size={15} />
              </a>
            </article>
          </div>
        </section>

        <section className={styles.providersSection} id="providers" aria-labelledby="providers-title">
          <div className={styles.providersIntro}>
            <span className={styles.sectionEyebrow}>CONNECTED THROUGH ADAPTERS</span>
            <h2 id="providers-title">Start with what<br />you already have.</h2>
            <p>Provider support is evolving. Check the status and test the operations you need before trusting a connection with important files.</p>
            <a className={styles.textLink} href={`${docsUrl}providers/`}>
              View provider status <ArrowRight size={16} />
            </a>
          </div>
          <div className={styles.providerList}>
            {providers.map(({ name, status, icon: Icon, tone }) => (
              <div className={styles.providerRow} key={name}>
                <span className={`${styles.providerIcon} ${styles[`providerIcon_${tone}`]}`}><Icon size={18} strokeWidth={1.7} /></span>
                <span className={styles.providerName}>{name}</span>
                <span className={`${styles.providerStatus} ${status === "Beta" ? styles.betaStatus : styles.availableStatus}`}>
                  <i /> {status}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className={styles.betaNote} aria-label="Beta notice">
          <span className={styles.betaMark}>i</span>
          <p><strong>UMedia is in early beta.</strong> Provider maturity and operation coverage vary. This preview does not offer public account signup.</p>
          <a href={`${docsUrl}roadmap/`}>See current limitations <ArrowUpRight size={14} /></a>
        </section>

        <section className={styles.closingSection}>
          <span className={styles.sectionEyebrow}>OPEN SOURCE. SELF-HOSTED. YOURS.</span>
          <h2>A calmer home<br />for your media.</h2>
          <p>Explore the project, read the setup guide, or help shape what comes next.</p>
          <div className={styles.closingActions}>
            <ActionLink href={`${docsUrl}getting-started/`} primary>Get started</ActionLink>
            <ActionLink href="https://github.com/mahdikiani/umedia/issues">Contribute on GitHub</ActionLink>
          </div>
        </section>
      </main>

      <footer className={styles.footer}>
        <div className={styles.footerInner}>
          <Link className={styles.brand} href="/">
            <BrandMark /><span>UMedia</span>
          </Link>
          <p>Open source, self-hosted media management.</p>
          <nav className={styles.footerLinks} aria-label="Footer navigation">
            <a href={docsUrl}>Documentation</a>
            <a href="https://github.com/mahdikiani/umedia/issues">Support</a>
            <a href="mailto:mahdikiany@gmail.com">Contact</a>
            <a href="https://github.com/mahdikiani/umedia" aria-label="UMedia on GitHub"><Code2 size={17} /></a>
          </nav>
          <span className={styles.copyright}>© 2026 Mahdi Kiani · MIT License</span>
        </div>
      </footer>
    </div>
  );
}
