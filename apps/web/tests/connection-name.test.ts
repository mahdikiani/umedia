import { describe, expect, it } from "vitest";

import {
  connectionNameError,
  suggestConnectionName,
} from "@/lib/connection-name";

describe("connection names (S3 bucket rules)", () => {
  it.each(["abc", "my-nas", "r2-media", "x".repeat(63), "media-2"])(
    "accepts %s",
    (name) => {
      expect(connectionNameError(name)).toBeNull();
    },
  );

  it.each([
    ["ab", /3 and 63/],
    ["x".repeat(64), /3 and 63/],
    ["My NAS", /lowercase/],
    ["my.nas", /lowercase/],
    ["-nas", /start and end/],
    ["my--nas", /consecutive/],
    ["xn--abc", /xn--/],
    ["nas-s3alias", /-s3alias/],
    ["umedia", /reserved/],
    ["فایل", /lowercase/],
  ])("rejects %s", (name, reason) => {
    expect(connectionNameError(name)).toMatch(reason);
  });

  it.each([
    ["Microsoft OneDrive", "microsoft-onedrive"],
    ["FTP / FTPS", "ftp-ftps"],
    ["Hugging Face Buckets", "hugging-face-buckets"],
    ["S3", "s3-storage"],
    ["فایل‌های من", "storage"],
  ])("suggests %s -> %s", (raw, slug) => {
    expect(suggestConnectionName(raw)).toBe(slug);
    expect(connectionNameError(slug)).toBeNull();
  });

  it("suggests a name the user does not already use", () => {
    expect(suggestConnectionName("SFTP", ["sftp", "sftp-2"])).toBe("sftp-3");
  });
});
