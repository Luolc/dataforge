// The deliverables whose path a directory of the attempt gives in place of
// the record's (/newest): the project names where to look, and the page
// shows each with its path and the directory it came from.
import { z } from "zod";

import {
  getJson,
  type Loaded,
  urls,
  useLoaded,
  useRefresh,
} from "../../api/client";
import type { AttemptRecord, Deliverable, Project } from "../../api/types";

const newestPaths = z.record(
  z.string(),
  z.looseObject({ path: z.string(), dir: z.string() }),
);
type NewestPaths = z.infer<typeof newestPaths>;

/** /newest for an attempt; nothing is read for a project that names none. */
export function useNewest(
  project: Project,
  attempt: string,
): Loaded<NewestPaths> {
  const url = (project.deliverables ?? []).some((d) => d.newest === true)
    ? urls.newest(project.id, attempt)
    : null;
  return useLoaded(
    url,
    (signal) => getJson(url ?? "", newestPaths, signal),
    useRefresh(),
  );
}

/**
 * The record with each deliverable /newest names at the path it gives, from
 * the directory it gives and from no run; one the record lacks is added as
 * the project declares it. The record as it is while /newest is loading or
 * failed.
 */
export function withNewest(
  project: Project,
  record: AttemptRecord,
  loaded: Loaded<NewestPaths>,
): AttemptRecord {
  if (loaded.state !== "ok" || Object.keys(loaded.data).length === 0)
    return record;
  const found = loaded.data;
  const placed = (d: Deliverable, at: { path: string; dir: string }) => ({
    ...d,
    path: at.path,
    from_dir: at.dir,
    visit: null,
    visit_index: null,
    reason: null,
  });
  const have = record.deliverables ?? [];
  const added = (project.deliverables ?? []).flatMap((d) => {
    const at = found[d.name];
    if (at === undefined || have.some((h) => h.name === d.name)) return [];
    const blank: Deliverable = {
      name: d.name,
      kind: d.kind,
      title: d.title,
      state: "",
      output: null,
      visit: null,
      visit_index: null,
      path: null,
      reason: null,
    };
    return [placed(blank, at)];
  });
  return {
    ...record,
    deliverables: [
      ...have.map((d) => {
        const at = found[d.name];
        return at === undefined ? d : placed(d, at);
      }),
      ...added,
    ],
  };
}
