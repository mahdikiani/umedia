"use client";

import { File as FileIcon, FolderOpen } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useLocale } from "@/components/locale-provider";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api, fileContentUrl, type MediaFileItem } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { formatBytes } from "@/lib/format";
import { readRecents, type RecentItem } from "@/lib/recents";

async function hydrate(entries: RecentItem[]): Promise<MediaFileItem[]> {
  const resolved = await Promise.all(
    entries.map(async (entry) => {
      try {
        return await api<MediaFileItem>(`/files/${entry.uid}`);
      } catch {
        return null;
      }
    }),
  );
  return resolved.filter((item): item is MediaFileItem => item !== null);
}

export default function HomePage() {
  const { locale } = useLocale();
  const text = useCopy(locale);
  const [files, setFiles] = useState<MediaFileItem[]>([]);
  const [folders, setFolders] = useState<MediaFileItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const recents = readRecents();
    Promise.all([hydrate(recents.files), hydrate(recents.folders)])
      .then(([nextFiles, nextFolders]) => {
        setFiles(nextFiles);
        setFolders(nextFolders);
      })
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">{text.homeTitle}</h1>
      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{text.recentFiles}</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="text-sm text-muted-foreground">…</p>
            ) : files.length === 0 ? (
              <p className="text-sm text-muted-foreground">{text.noRecentFiles}</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {files.map((item) => (
                  <li key={item.uid}>
                    <a
                      className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted"
                      href={fileContentUrl(item.uid, item.name)}
                      rel="noreferrer"
                      target="_blank"
                    >
                      <FileIcon className="text-muted-foreground" size={16} />
                      <span className="min-w-0 flex-1 truncate">{item.name}</span>
                      <span className="text-xs text-muted-foreground tabular-nums">
                        {formatBytes(item.size)}
                      </span>
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{text.recentFolders}</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="text-sm text-muted-foreground">…</p>
            ) : folders.length === 0 ? (
              <p className="text-sm text-muted-foreground">{text.noRecentFolders}</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {folders.map((item) => (
                  <li key={item.uid}>
                    <Link
                      className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted"
                      href={`/files?folder=${encodeURIComponent(item.uid)}`}
                    >
                      <FolderOpen className="text-muted-foreground" size={16} />
                      <span className="min-w-0 flex-1 truncate">{item.name}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
