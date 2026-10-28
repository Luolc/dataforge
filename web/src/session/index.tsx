// The session view of a trace: a claude-code-stream-json trace parsed in the
// browser and drawn as a session, any other format shown as text.
import { type ReactNode, useMemo } from "react";

import {
  ApiError,
  type FileSource,
  useFileSource,
  useLoaded,
} from "../api/client";
import type { Project } from "../api/types";
import { parseTrace, type Session, transcriptOutput } from "./parse";
import { SessionBody, type SessionTotals, type TraceLength } from "./Session";

const STREAM_JSON = "claude-code-stream-json";

/** A file's text; a refusal, such as a 413 for a file over FILE_LIMIT, is an ApiError with its reason. */
async function getText(read: Promise<Response>): Promise<string> {
  const response = await read;
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

/** Where a session's transcripts are, under its trace's directory. */
const TRANSCRIPTS = "sessions/projects";

/**
 * The output tokens of a session's last call from its transcript, which
 * Claude Code keeps at sessions/projects/<project>/<session ID>.jsonl beside
 * a Harbor trial's trace; null when no transcript there gives them.
 */
async function finalOutput(
  source: FileSource,
  project: string,
  attempt: string,
  dir: string,
  sessionId: string,
  id: string,
  signal: AbortSignal,
): Promise<number | null> {
  const root = dir === "" ? TRANSCRIPTS : `${dir}/${TRANSCRIPTS}`;
  // A trace with no transcripts beside it has no such directory; any other
  // failure to read them is not the same as their not being there.
  const listed = await source
    .listing(project, attempt, root, signal)
    .catch((error: unknown) => {
      if (error instanceof ApiError && error.status === 404)
        return { entries: [] };
      throw error;
    });
  for (const entry of listed.entries) {
    if (entry.type !== "dir") continue;
    const path = `${root}/${entry.name}/${sessionId}.jsonl`;
    const response = await source.file(project, attempt, path, signal);
    if (response.status === 404) continue;
    if (!response.ok)
      throw new ApiError(`${path}: ${response.status.toString()}`);
    const output = transcriptOutput(await response.text(), id);
    if (output !== null) return output;
  }
  return null;
}

const NO_USAGE = "The trace gives no token counts for its last call.";
const NOT_FINAL =
  "The final output of its last call is not known: the trace gives it only for a session of one call, in its result line, and no transcript of the session beside the trace records the call.";

/**
 * The session's trace length: its last call's input and its final output,
 * read from the trace itself when told the trace holds the final count or
 * when its result line gives it, else from the session's transcript.
 */
function useTraceLength(
  project: string,
  attempt: string,
  path: string,
  session: Session | null,
  output: "transcript" | "trace",
): TraceLength {
  const source = useFileSource();
  const last = session?.last ?? null;
  const sessionId = session?.sessionId ?? null;
  const reads =
    output === "transcript" &&
    last?.input != null &&
    last.final === null &&
    last.id !== null &&
    sessionId !== null;
  const loaded = useLoaded(
    reads ? JSON.stringify([project, attempt, path, "transcript"]) : null,
    (signal) =>
      finalOutput(
        source,
        project,
        attempt,
        path.slice(0, Math.max(path.lastIndexOf("/"), 0)),
        sessionId ?? "",
        last?.id ?? "",
        signal,
      ),
  );
  if (last?.input == null) return { state: "none", why: NO_USAGE };
  if (output === "trace")
    return last.output === null
      ? { state: "none", why: NO_USAGE }
      : { state: "ok", tokens: last.input + last.output };
  if (last.final !== null)
    return { state: "ok", tokens: last.input + last.final };
  if (!reads) return { state: "none", why: NOT_FINAL };
  if (loaded.state === "loading") return { state: "loading" };
  if (loaded.state === "error")
    return {
      state: "none",
      why: `The session's transcripts could not be read: ${loaded.message}.`,
    };
  if (loaded.data === null) return { state: "none", why: NOT_FINAL };
  return { state: "ok", tokens: last.input + loaded.data };
}

export function SessionView({
  project,
  attempt,
  path,
  format,
  badge,
  totals,
  output = "transcript",
}: {
  /** The project's ID and its sessions' thinking and start_open. */
  project: Pick<Project, "id" | "session">;
  attempt: string;
  /** The trace, relative to the attempt's directory. */
  path: string;
  format: string | null;
  /** The result at the head of the session, such as a trial's reward. */
  badge?: ReactNode;
  /** Which totals the header shows, as in SessionBody. */
  totals?: SessionTotals;
  /**
   * Where the final output of the last call is read: from the session's
   * transcript beside the trace, or from the trace, for one whose writer put
   * the final count there, as an export does.
   */
  output?: "transcript" | "trace";
}) {
  const source = useFileSource();
  const loaded = useLoaded(
    JSON.stringify([project.id, attempt, path]),
    (signal) => getText(source.file(project.id, attempt, path, signal)),
  );
  const text = loaded.state === "ok" ? loaded.data : null;
  const session = useMemo(
    () => (text !== null && format === STREAM_JSON ? parseTrace(text) : null),
    [text, format],
  );
  const traceLength = useTraceLength(
    project.id,
    attempt,
    path,
    session,
    output,
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
        traceLength={traceLength}
        startOpen={project.session.start_open === true}
        {...(totals === undefined ? {} : { totals })}
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
      <pre className="overflow-x-auto px-5 py-3 font-mono text-code whitespace-pre-wrap">
        {loaded.data}
      </pre>
    </section>
  );
}
