import type { MediaFileItem } from "@/lib/api";
import { fileContentUrl } from "@/lib/api";

const GENERIC_MIMES = new Set([
  "",
  "application/octet-stream",
  "binary/octet-stream",
  "application/x-unknown",
]);

const EXT_TO_MIME: Record<string, string> = {
  pdf: "application/pdf",
  txt: "text/plain",
  md: "text/plain",
  markdown: "text/plain",
  html: "text/html",
  htm: "text/html",
  css: "text/css",
  csv: "text/csv",
  json: "application/json",
  js: "application/javascript",
  mjs: "application/javascript",
  ts: "application/javascript",
  zip: "application/zip",
  rar: "application/x-rar-compressed",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  gif: "image/gif",
  svg: "image/svg+xml",
  webp: "image/webp",
  ico: "image/x-icon",
  tiff: "image/tiff",
  tif: "image/tiff",
  psd: "image/vnd.adobe.photoshop",
  mp4: "video/mp4",
  mpeg: "video/mpeg",
  mpg: "video/mpeg",
  mov: "video/quicktime",
  avi: "video/x-msvideo",
  mp3: "audio/mpeg",
  wav: "audio/wav",
  doc: "application/msword",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xls: "application/vnd.ms-excel",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ppt: "application/vnd.ms-powerpoint",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
};

const CHIP_LABELS: Record<string, string> = {
  "inode/directory": "Folder",
  "application/pdf": "PDF",
  "application/json": "JSON",
  "application/zip": "ZIP",
  "application/javascript": "JS",
  "text/javascript": "JS",
  "text/plain": "TXT",
  "text/html": "HTML",
  "text/css": "CSS",
  "text/csv": "CSV",
  "image/jpeg": "JPEG",
  "image/jpg": "JPEG",
  "image/png": "PNG",
  "image/gif": "GIF",
  "image/svg+xml": "SVG",
  "image/webp": "WEBP",
  "video/mp4": "MP4",
  "audio/mpeg": "MP3",
  "audio/wav": "WAV",
  "application/msword": "DOC",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
    "DOCX",
  "application/vnd.ms-excel": "XLS",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
  "application/vnd.ms-powerpoint": "PPT",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation":
    "PPTX",
};

const MIME_TO_ICON: Record<string, string> = {
  "inode/directory": "folder-1485.svg",
  "application/msword": "text_color_doc.svg",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
    "text_color_doc.svg",
  "application/vnd.ms-excel": "text_color_xls.svg",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
    "text_color_xls.svg",
  "application/vnd.ms-powerpoint": "text_color_ppt.svg",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation":
    "text_color_ppt.svg",
  "application/pdf": "text_color_pdf.svg",
  "application/zip": "text_color_zip.svg",
  "application/x-rar-compressed": "text_color_rar.svg",
  "application/x-msdownload": "text_color_exe.svg",
  "application/json": "text_color_json.svg",
  "application/javascript": "text_color_js.svg",
  "text/javascript": "text_color_js.svg",
  "text/css": "text_color_css.svg",
  "text/csv": "text_color_csv.svg",
  "text/html": "text_color_html.svg",
  "text/x-java-source": "text_color_java.svg",
  "image/jpeg": "text_color_jpeg.svg",
  "image/jpg": "text_color_jpeg.svg",
  "image/png": "text_color_png.svg",
  "image/gif": "text_color_gif.svg",
  "image/svg+xml": "text_color_svg.svg",
  "image/webp": "text_color_webp.svg",
  "image/vnd.adobe.photoshop": "text_color_psd.svg",
  "image/x-icon": "text_color_ico.svg",
  "image/tiff": "text_color_tiff.svg",
  "video/mp4": "text_color_mp4.svg",
  "video/mpeg": "text_color_mpg.svg",
  "video/quicktime": "text_color_mov.svg",
  "video/x-msvideo": "text_color_avi.svg",
  "audio/mpeg": "text_color_mp3.svg",
  "audio/wav": "text_color_wav.svg",
  "text/plain": "text_color_txt.svg",
  "application/vnd.sketch": "text_color_skt.svg",
  "application/postscript": "text_color_ai.svg",
  "application/vnd.cdr": "text_color_cdr.svg",
  "application/x-blender": "text_color_blend.svg",
  "application/x-cinema4d": "text_color_c4d.svg",
  "application/x-adobe-aftereffects": "text_color_aep.svg",
  "application/x-hdf5": "icon_color_h5.svg",
  "application/vnd.figma": "text_color_fig.svg",
};

const UNKNOWN_ICON = "unknown_flat_ico.svg";

const ICON_FILES = new Set([
  "folder-1485.svg",
  "icon_color_audio.svg",
  "icon_color_bar.svg",
  "icon_color_code.svg",
  "icon_color_doc.svg",
  "icon_color_fig.svg",
  "icon_color_file.svg",
  "icon_color_h5.svg",
  "icon_color_img.svg",
  "icon_color_page.svg",
  "icon_color_pie.svg",
  "icon_color_sheet.svg",
  "icon_color_skt.svg",
  "icon_color_video.svg",
  "text_color_aep.svg",
  "text_color_ai.svg",
  "text_color_avi.svg",
  "text_color_blend.svg",
  "text_color_c4d.svg",
  "text_color_cdr.svg",
  "text_color_css.svg",
  "text_color_csv.svg",
  "text_color_dmg.svg",
  "text_color_doc.svg",
  "text_color_exe.svg",
  "text_color_fig.svg",
  "text_color_gif.svg",
  "text_color_html.svg",
  "text_color_ico.svg",
  "text_color_java.svg",
  "text_color_jpeg.svg",
  "text_color_jpg.svg",
  "text_color_js.svg",
  "text_color_json.svg",
  "text_color_mov.svg",
  "text_color_mp3.svg",
  "text_color_mp4.svg",
  "text_color_mpg.svg",
  "text_color_pdf.svg",
  "text_color_png.svg",
  "text_color_ppt.svg",
  "text_color_psd.svg",
  "text_color_rar.svg",
  "text_color_skt.svg",
  "text_color_svg.svg",
  "text_color_tiff.svg",
  "text_color_txt.svg",
  "text_color_wav.svg",
  "text_color_webp.svg",
  "text_color_xls.svg",
  "text_color_zip.svg",
  UNKNOWN_ICON,
]);

export type FilePreviewKind = "image" | "video" | "icon";

export type FileGlyph =
  | "folder"
  | "pdf"
  | "image"
  | "video"
  | "audio"
  | "archive"
  | "code"
  | "spreadsheet"
  | "presentation"
  | "text"
  | "file";

export type FileTypeSource = {
  type: string;
  name: string;
  content_type: string;
};

function normalizeMime(raw: string | null | undefined): string {
  return (raw ?? "").split(";")[0].trim().toLowerCase();
}

function extensionOf(name: string): string | null {
  const base = name.split("/").pop() ?? name;
  const dot = base.lastIndexOf(".");
  if (dot <= 0 || dot === base.length - 1) return null;
  return base.slice(dot + 1).toLowerCase();
}

export function resolvedMime(item: FileTypeSource): string {
  if (item.type === "folder") return "inode/directory";
  const mime = normalizeMime(item.content_type);
  if (mime && !GENERIC_MIMES.has(mime)) return mime;
  const ext = extensionOf(item.name);
  if (ext && EXT_TO_MIME[ext]) return EXT_TO_MIME[ext];
  return mime || "application/octet-stream";
}

export function mimeChipLabel(item: FileTypeSource): string {
  if (item.type === "folder") return "Folder";
  const mime = resolvedMime(item);
  if (CHIP_LABELS[mime]) return CHIP_LABELS[mime];
  if (GENERIC_MIMES.has(mime)) {
    const ext = extensionOf(item.name);
    return ext ? ext.toUpperCase().slice(0, 8) : "File";
  }
  const subtype = mime.split("/")[1] ?? mime;
  const token = subtype.replace(/^x-/, "").split(".").pop()?.split("+")[0];
  if (!token || token === "octet-stream") {
    const ext = extensionOf(item.name);
    return ext ? ext.toUpperCase().slice(0, 8) : "File";
  }
  return token.toUpperCase().slice(0, 8);
}

export function typeFilterKey(item: FileTypeSource): string {
  if (item.type === "folder") return "folder";
  return resolvedMime(item);
}

export function matchesTypeFilter(
  item: MediaFileItem,
  filter: string | null,
): boolean {
  if (!filter) return true;
  return typeFilterKey(item) === filter;
}

export function fileGlyph(item: FileTypeSource): FileGlyph {
  if (item.type === "folder") return "folder";
  const mime = resolvedMime(item);
  if (mime === "application/pdf") return "pdf";
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  if (
    mime === "application/zip"
    || mime === "application/x-rar-compressed"
    || mime === "application/x-7z-compressed"
    || mime === "application/gzip"
  ) {
    return "archive";
  }
  if (
    mime === "application/json"
    || mime === "application/javascript"
    || mime === "text/javascript"
    || mime === "text/css"
    || mime === "text/html"
    || mime === "text/x-java-source"
    || mime.endsWith("+json")
  ) {
    return "code";
  }
  if (
    mime === "application/vnd.ms-excel"
    || mime === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    || mime === "text/csv"
  ) {
    return "spreadsheet";
  }
  if (
    mime === "application/vnd.ms-powerpoint"
    || mime
      === "application/vnd.openxmlformats-officedocument.presentationml.presentation"
  ) {
    return "presentation";
  }
  if (mime.startsWith("text/") || mime === "application/msword" || mime.includes("wordprocessing")) {
    return "text";
  }
  return "file";
}

export function fileIconName(item: FileTypeSource): string {
  if (item.type === "folder") return "folder-1485.svg";
  const mime = resolvedMime(item);
  if (MIME_TO_ICON[mime]) return MIME_TO_ICON[mime];
  const ext = extensionOf(item.name);
  if (ext) {
    const byExt = `text_color_${ext}.svg`;
    if (ICON_FILES.has(byExt)) return byExt;
  }
  if (mime.startsWith("image/")) return "icon_color_img.svg";
  if (mime.startsWith("video/")) return "icon_color_video.svg";
  if (mime.startsWith("audio/")) return "icon_color_audio.svg";
  return UNKNOWN_ICON;
}

export function fileIconUrl(item: FileTypeSource): string {
  return `/api/v1/statics/${fileIconName(item)}`;
}

export function filePreviewKind(item: MediaFileItem): FilePreviewKind {
  if (item.type === "folder") return "icon";
  const mime = resolvedMime(item);
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  return "icon";
}

export function filePreviewSrc(item: MediaFileItem): string | null {
  if (filePreviewKind(item) === "icon") return null;
  return fileContentUrl(item.uid, item.name);
}
