import type { Metadata } from "next";
import Link from "next/link";
import {
  ArrowRight,
  ArrowUpRight,
  Bell,
  Code2,
  Cloud,
  Database,
  FileImage,
  FileText,
  FolderClosed,
  FolderPlus,
  HardDrive,
  Home as HomeIcon,
  Languages,
  LayoutGrid,
  List,
  Search,
  Settings,
  MoveRight,
  ShieldCheck,
  Star,
  Trash2,
  Upload,
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
      <div className={`${styles.previewShell} ${styles.panelPreview}`} aria-label="Illustrative preview of the UMedia Files page">
        <div className={styles.previewSurface}>
          <div className={styles.windowBar}>
            <div className={styles.windowBrand}>
              <BrandMark />
              <span>UMedia</span>
            </div>
            <span className={styles.windowSearch}><Search size={13} /> Search files…</span>
            <span className={styles.windowTools} aria-hidden="true"><Languages size={13} /><span>ع</span><span className={styles.windowAvatar}>M</span></span>
          </div>
          <div className={styles.libraryLayout}>
            <aside className={styles.librarySidebar} aria-label="UMedia navigation preview">
              <span className={styles.sideItem}><HomeIcon size={14} /> Home</span>
              <span className={`${styles.sideItem} ${styles.sideItemActive}`}>
                <FileText size={14} /> Files
              </span>
              <span className={styles.sideItem}><Bell size={14} /> Notifications</span>
              <div className={styles.sideDivider} />
              <span className={styles.sideItem}><Star size={14} /> Starred</span>
              <span className={styles.sideItem}><Trash2 size={14} /> Trash</span>
              <div className={styles.sidebarSpacer} />
              <span className={styles.sideItem}><Database size={14} /> Storage settings</span>
              <span className={styles.sideItem}><Settings size={14} /> Settings</span>
            </aside>
            <div className={styles.libraryContent}>
              <div className={styles.fileToolbar}>
                <span className={styles.breadcrumb}><HomeIcon size={12} /> <span>/</span> Files</span>
                <div className={styles.toolbarButtons} aria-hidden="true">
                  <span><Upload size={12} /> Upload</span>
                  <span><FolderPlus size={12} /> New folder</span>
                </div>
              </div>
              <div className={styles.fileOptions} aria-hidden="true">
                <span>Group: None <span>⌄</span></span>
                <span className={styles.viewOptions}><b><List size={12} /> List</b><i><LayoutGrid size={12} /> Cards</i></span>
              </div>
              <div className={styles.fileTable}>
                <div className={styles.fileHeader}><span>Name</span><span>Type</span><span>Size</span><span>Modified</span></div>
                <div className={styles.fileRow}><span className={styles.fileCellName}><FolderClosed size={15} /> Documents</span><span>Folder</span><span>—</span><span>Today</span></div>
                <div className={styles.fileRow}><span className={styles.fileCellName}><FileImage size={15} /> Photo.jpg</span><span>Image</span><span>2.4 MB</span><span>Yesterday</span></div>
                <div className={styles.fileRow}><span className={styles.fileCellName}><FileText size={15} /> Notes.pdf</span><span>PDF</span><span>840 KB</span><span>Sep 12</span></div>
              </div>
              <span className={styles.sampleDataNote}>Sample files shown</span>
            </div>
          </div>
        </div>
      </div>
      <figcaption className="sr-only">
        An illustrative preview of the UMedia Files page. The navigation, toolbar, and table follow the app; listed files are sample data.
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
