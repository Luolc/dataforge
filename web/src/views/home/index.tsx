import { useCallback, useEffect, useState } from "react";

import { getJson, urls } from "../../api/client";
import { type AttemptRow, attemptRows, type Project } from "../../api/types";
import { Note } from "../../ui/card";
import { HomeTable } from "./HomeTable";

export interface Read {
  rows: AttemptRow[];
  /** When the listing arrived, as ISO 8601. */
  at: string;
}

/**
 * A project's attempt listing, read once and again on each refresh, never
 * on a timer; the last read stays shown while the next one loads.
 */
function useListing(project: string) {
  const [generation, setGeneration] = useState(0);
  const key = JSON.stringify([project, generation]);
  const [read, setRead] = useState<Read & { project: string }>();
  const [done, setDone] = useState<{ key: string; error: string | null }>();
  useEffect(() => {
    const controller = new AbortController();
    getJson(urls.attempts(project), attemptRows, controller.signal).then(
      (rows) => {
        setRead({ project, rows, at: new Date().toISOString() });
        setDone({ key, error: null });
      },
      (e: unknown) => {
        if (controller.signal.aborted) return;
        setDone({ key, error: e instanceof Error ? e.message : String(e) });
      },
    );
    return () => {
      controller.abort();
    };
  }, [project, key]);
  const refresh = useCallback(() => {
    setGeneration((g) => g + 1);
  }, []);
  return {
    read: read?.project === project ? read : null,
    error: done?.key === key ? done.error : null,
    reading: done?.key !== key,
    refresh,
  };
}

/** A project's home page: one row per data item (design-viz.md section 5). */
export default function Home({ project }: { project: Project }) {
  const { read, error, reading, refresh } = useListing(project.id);
  if (read === null)
    return <Note>{error ?? "Loading…"}</Note>;
  return (
    <HomeTable
      project={project}
      read={read}
      reading={reading}
      error={error}
      onRefresh={refresh}
    />
  );
}
