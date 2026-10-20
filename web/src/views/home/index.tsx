import { useCallback, useEffect, useState } from "react";

import { getJson, urls } from "../../api/client";
import { usePolling } from "../../api/poll";
import { attemptRows, type Project } from "../../api/types";
import { Note } from "../../ui/card";
import { HomeTable, type Read } from "./HomeTable";

/**
 * A project's attempt listing, read once, again on each refresh, and every
 * POLL_MS while an attempt of it runs; the last read stays shown while the
 * next one loads. Only a refresh says it is reading.
 */
function useListing(project: string) {
  const [generation, setGeneration] = useState({ n: 0, polled: false });
  const key = JSON.stringify([project, generation.n]);
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
    setGeneration((g) => ({ n: g.n + 1, polled: false }));
  }, []);
  const shown = read?.project === project ? read : null;
  usePolling(shown?.rows.some((r) => r.live === "running") ?? false, () => {
    setGeneration((g) => ({ n: g.n + 1, polled: true }));
  });
  return {
    read: shown,
    error: done?.key === key ? done.error : null,
    reading: done?.key !== key && !generation.polled,
    refresh,
  };
}

/** A project's home page: one row per data item (design-viz.md section 5). */
export default function Home({ project }: { project: Project }) {
  const { read, error, reading, refresh } = useListing(project.id);
  if (read === null) return <Note>{error ?? "Loading…"}</Note>;
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
