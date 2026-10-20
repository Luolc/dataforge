// A file of an attempt, read through the files API as bytes and shown as
// text only when it is UTF-8.
import { ApiError, urls, useLoaded } from "../../api/client";

export type FileContent =
  | { kind: "text"; text: string; size: number }
  /** Not UTF-8: shown only by its size. */
  | { kind: "binary"; size: number }
  /** Over the server's FILE_LIMIT: the 413's reason, which gives the size. */
  | { kind: "too-large"; reason: string };

const utf8 = new TextDecoder("utf-8", { fatal: true });

/** GET a file's bytes; any failure but a 413 is an ApiError. */
async function fetchFile(
  project: string,
  attempt: string,
  path: string,
  signal?: AbortSignal,
): Promise<FileContent> {
  const response = await fetch(
    urls.files(project, attempt, path),
    signal ? { signal } : {},
  );
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const reason =
      typeof body === "object" &&
      body !== null &&
      "reason" in body &&
      typeof body.reason === "string"
        ? body.reason
        : response.statusText;
    if (response.status === 413) return { kind: "too-large", reason };
    throw new ApiError(`${response.status.toString()}: ${reason}`);
  }
  const bytes = await response.arrayBuffer();
  try {
    return { kind: "text", text: utf8.decode(bytes), size: bytes.byteLength };
  } catch {
    return { kind: "binary", size: bytes.byteLength };
  }
}

/** fetchFile as a hook; a null path loads nothing. */
export function useFile(project: string, attempt: string, path: string | null) {
  return useLoaded(
    path === null ? null : JSON.stringify([project, attempt, path]),
    (signal) => fetchFile(project, attempt, path ?? "", signal),
  );
}

/** A size in bytes as "576 B", "4.4 KiB" or "16.0 MiB". */
export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes.toString()} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}
