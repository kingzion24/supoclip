import { formatSupportMessage, parseApiError } from "@/lib/api-error";

export const EXPORT_PRESETS = [
  { id: "tiktok", label: "TikTok", width: 1080, height: 1920, bitrate: 10_000_000 },
  { id: "reels", label: "Reels", width: 1080, height: 1920, bitrate: 12_000_000 },
  { id: "shorts", label: "Shorts", width: 1080, height: 1920, bitrate: 10_000_000 },
  { id: "square", label: "Square 1:1", width: 1080, height: 1080, bitrate: 8_000_000 },
  { id: "landscape", label: "Landscape 16:9", width: 1920, height: 1080, bitrate: 12_000_000 },
] as const;

export function getClipUrl(videoUrl: string, version?: string) {
  const url = videoUrl.startsWith("/api/") ? videoUrl : `/api${videoUrl}`;
  return version ? `${url}${url.includes("?") ? "&" : "?"}v=${encodeURIComponent(version)}` : url;
}

export async function requestAction(url: string, method: string, body?: unknown) {
  const response = await fetch(url, {
    method,
    ...(body === undefined ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  });
  if (!response.ok) {
    throw new Error(formatSupportMessage(await parseApiError(response, "Could not save changes. Please try again.")));
  }
  return response;
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Give the browser time to begin the download before releasing the URL.
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

type DirectoryPicker = (options?: {
  id?: string;
  mode?: "read" | "readwrite";
  startIn?: string;
}) => Promise<FileSystemDirectoryHandle>;

/** Turn a clip title into a safe Windows/macOS file name. */
export function clipFileName(order: number, title: string | null | undefined, fallback: string, suffix = "") {
  const slug = (title || "")
    .normalize("NFKD")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .slice(0, 60);
  const base = slug || fallback.replace(/\.mp4$/i, "");
  return `${String(order).padStart(2, "0")}-${base}${suffix}.mp4`;
}

/**
 * Save files into a folder the user picks (Chrome/Edge). Returns false when the
 * browser cannot pick folders, so the caller can fall back to downloads.
 */
export async function saveFilesToFolder(
  files: { name: string; load: () => Promise<Blob> }[],
  onProgress?: (done: number, total: number) => void,
): Promise<boolean> {
  const picker = (window as unknown as { showDirectoryPicker?: DirectoryPicker }).showDirectoryPicker;
  if (!picker) return false;
  // The id makes the browser reopen the folder chosen last time.
  const folder = await picker({ id: "katakata-clips", mode: "readwrite", startIn: "videos" });
  for (const [index, file] of files.entries()) {
    const blob = await file.load();
    const handle = await folder.getFileHandle(file.name, { create: true });
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
    onProgress?.(index + 1, files.length);
  }
  return true;
}
