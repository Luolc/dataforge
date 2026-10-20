// The session view of a trace: a claude-code-stream-json trace parsed in the
// browser and drawn as a session, any other format shown as text.
import { type ReactNode, useMemo } from "react";

import { ApiError, urls, useLoaded } from "../api/client";
import type { Project } from "../api/types";
import { parseTrace } from "./parse";
import { SessionBody } from "./Session";

export const STREAM_JSON = "claude-code-stream-json";

/** A file's text; a refusal, such as a 413 for a file over FILE_LIMIT, is an ApiError with its reason. */
export async function getText(
  url: string,
  signal?: AbortSignal,
): Promise<string> {
  const response = await fetch(url, signal ? { signal } : {});
  if (response.ok) return response.text();
  const body: unknown = await response.json().catch(() => null);
  const reason =
    typeof body === "object" &&
    body !== null &&
    "reason" in body &&
    typeof body.reason === "string"
      ? body.reason
      : response.statusText;
  throw new ApiError(`${response.status.toString()}: ${reason}`);
}

export function SessionView({
  project,
  attempt,
  path,
  format,
  badge,
}: {
  project: Project;
  attempt: string;
  /** The trace, relative to the attempt's directory. */
  path: string;
  format: string | null;
  /** The result at the head of the session, such as a trial's reward. */
  badge?: ReactNode;
}) {
  const url = urls.files(project.id, attempt, path);
  const loaded = useLoaded(url, (signal) => getText(url, signal));
  const text = loaded.state === "ok" ? loaded.data : null;
  const session = useMemo(
    () => (text !== null && format === STREAM_JSON ? parseTrace(text) : null),
    [text, format],
  );
  if (loaded.state === "loading")
    return (
      <p className="p-5 text-fg-3">
        Loading the trace <code className="font-mono text-xs">{path}</code>…
      </p>
    );
  if (loaded.state === "error") {
    return (
      <p className="rounded-panel border border-line p-5 text-fg-2">
        The trace <code className="font-mono text-xs">{path}</code> could not be
        read: {loaded.message}
      </p>
    );
  }
  if (session !== null) {
    return (
      <SessionBody
        session={session}
        thinking={project.session.thinking}
        badge={badge}
      />
    );
  }
  return (
    <section className="rounded-panel border border-line bg-panel">
      <header className="flex flex-wrap items-center gap-x-4 border-b border-line px-5 py-3 text-xs text-fg-2">
        {badge}
        <span>
          <span className="text-fg-3">format </span>
          <span className="font-mono">{format ?? "not given"}</span>, shown as
          text
        </span>
      </header>
      <pre className="overflow-x-auto px-5 py-3 font-mono text-xs text-fg whitespace-pre-wrap">
        {loaded.data}
      </pre>
    </section>
  );
}
