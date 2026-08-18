const STORAGE_KEY = "umedia.recents";
const MAX_ITEMS = 12;

export type RecentKind = "file" | "folder";

export type RecentItem = {
  uid: string;
  name: string;
  type: RecentKind;
  at: number;
};

type RecentsState = {
  files: RecentItem[];
  folders: RecentItem[];
};

function emptyState(): RecentsState {
  return { files: [], folders: [] };
}

export function readRecents(): RecentsState {
  if (typeof window === "undefined") return emptyState();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyState();
    const parsed = JSON.parse(raw) as Partial<RecentsState>;
    return {
      files: Array.isArray(parsed.files) ? parsed.files : [],
      folders: Array.isArray(parsed.folders) ? parsed.folders : [],
    };
  } catch {
    return emptyState();
  }
}

function writeRecents(state: RecentsState) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

export function rememberRecent(item: {
  uid: string;
  name: string;
  type: RecentKind;
}): void {
  if (typeof window === "undefined") return;
  const state = readRecents();
  const bucket = item.type === "folder" ? "folders" : "files";
  const next: RecentItem = {
    uid: item.uid,
    name: item.name,
    type: item.type,
    at: Date.now(),
  };
  state[bucket] = [
    next,
    ...state[bucket].filter((entry) => entry.uid !== item.uid),
  ].slice(0, MAX_ITEMS);
  writeRecents(state);
}
