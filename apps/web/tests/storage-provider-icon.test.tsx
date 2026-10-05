import "@testing-library/jest-dom/vitest";

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  connectionMeta,
  connectionProviderType,
  StorageProviderIcon,
} from "@/components/storage-provider-icon";

afterEach(cleanup);

describe("StorageProviderIcon", () => {
  it("renders Database cylinder with distinct classes per provider_type", () => {
    const { container: local } = render(
      <StorageProviderIcon providerType="local" />,
    );
    const { container: s3 } = render(
      <StorageProviderIcon providerType="s3" />,
    );
    const { container: drive } = render(
      <StorageProviderIcon providerType="google_drive" />,
    );
    const { container: telegram } = render(
      <StorageProviderIcon providerType="telegram" />,
    );

    const localEl = local.querySelector("[data-provider-type]")!;
    const s3El = s3.querySelector("[data-provider-type]")!;
    const driveEl = drive.querySelector("[data-provider-type]")!;
    const telegramEl = telegram.querySelector("[data-provider-type]")!;

    expect(localEl).toHaveAttribute("data-provider-type", "local");
    expect(s3El).toHaveAttribute("data-provider-type", "s3");
    expect(driveEl).toHaveAttribute("data-provider-type", "google_drive");
    expect(telegramEl).toHaveAttribute("data-provider-type", "telegram");

    expect(localEl.querySelector("svg")).toBeTruthy();
    expect(localEl.getAttribute("style")).not.toBe(s3El.getAttribute("style"));
    expect(s3El.getAttribute("style")).not.toBe(driveEl.getAttribute("style"));
    expect(driveEl.getAttribute("style")).not.toBe(
      telegramEl.getAttribute("style"),
    );
    expect(localEl.getAttribute("style")).not.toBe(
      driveEl.getAttribute("style"),
    );
  });

  it("normalizes gdrive alias to google_drive", () => {
    const { container } = render(
      <StorageProviderIcon providerType="gdrive" />,
    );
    expect(container.querySelector("[data-provider-type]")).toHaveAttribute(
      "data-provider-type",
      "google_drive",
    );
  });

  it("falls back to unknown styling for unrecognized types", () => {
    const { container: unknown } = render(
      <StorageProviderIcon providerType="weird_cloud" />,
    );
    const { container: local } = render(
      <StorageProviderIcon providerType="local" />,
    );
    const unknownEl = unknown.querySelector("[data-provider-type]")!;
    const localEl = local.querySelector("[data-provider-type]")!;
    expect(unknownEl).toHaveAttribute("data-provider-type", "weird_cloud");
    expect(unknownEl.getAttribute("style")).not.toBe(
      localEl.getAttribute("style"),
    );
  });
});

describe("connection helpers", () => {
  const connections = [
    { uid: "a", name: "Disk", provider_type: "local" },
    { uid: "b", name: "MinIO", provider_type: "s3" },
  ];

  it("resolves provider_type by connection id", () => {
    expect(connectionProviderType("b", connections)).toBe("s3");
    expect(connectionProviderType(null, connections)).toBeNull();
    expect(connectionProviderType("missing", connections)).toBeNull();
  });

  it("resolves name + providerType together", () => {
    expect(connectionMeta("a", connections)).toEqual({
      name: "Disk",
      providerType: "local",
      variant: null,
    });
  });
});

describe("provider logos", () => {
  afterEach(cleanup);

  const tile = (container: HTMLElement) =>
    container.querySelector("[data-provider-type]")!;

  it("uses official brand marks for branded services", () => {
    for (const providerType of ["dropbox", "google_drive", "telegram", "huggingface"]) {
      const { container } = render(<StorageProviderIcon providerType={providerType} />);
      expect(tile(container)).toHaveClass("storage-brand-tile");
      expect(tile(container).querySelector("svg path")).toBeTruthy();
    }
  });

  it("uses generic glyphs for protocols and logo-restricted brands", () => {
    for (const providerType of ["local", "ftp", "sftp", "webdav", "onedrive", "s3"]) {
      const { container } = render(<StorageProviderIcon providerType={providerType} />);
      expect(tile(container)).not.toHaveClass("storage-brand-tile");
    }
  });

  it("shows the service behind S3 and WebDAV from the variant", () => {
    const { container: r2 } = render(
      <StorageProviderIcon providerType="s3" variant="cloudflare" />,
    );
    const { container: plain } = render(<StorageProviderIcon providerType="s3" />);
    const { container: cloud } = render(
      <StorageProviderIcon providerType="webdav" variant="nextcloud" />,
    );

    expect(tile(r2)).toHaveClass("storage-brand-tile");
    expect(tile(r2)).toHaveAttribute("title", "Cloudflare R2");
    expect(tile(plain)).toHaveAttribute("title", "S3 compatible");
    expect(tile(cloud)).toHaveAttribute("title", "Nextcloud");
    expect(tile(r2)).toHaveAttribute("data-provider-variant", "cloudflare");
  });

  it("ignores an unknown variant", () => {
    const { container } = render(
      <StorageProviderIcon providerType="s3" variant="not-a-brand" />,
    );
    expect(tile(container)).toHaveAttribute("title", "S3 compatible");
  });
});
