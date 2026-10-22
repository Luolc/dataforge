// The versions an attempt has of each of its project's tabs, read with the
// page.
import {
  getJson,
  type Loaded,
  urls,
  useLoaded,
  useRefresh,
} from "../../api/client";
import {
  type Project,
  type ProjectTab,
  type Queued,
  type TabVersions,
  tabVersions,
} from "../../api/types";

/** The project's tabs with the versions the attempt has; none without tabs. */
export function useTabVersions(
  project: Project,
  attempt: string,
): Loaded<TabVersions[]> {
  const refresh = useRefresh();
  const url =
    (project.tabs ?? []).length > 0 ? urls.tabs(project.id, attempt) : null;
  return useLoaded(
    url,
    (signal) => getJson(url ?? "", tabVersions, signal),
    refresh,
  );
}

/** A tab's versions as read; none while loading or after a failure. */
export const versionsOf = (loaded: Loaded<TabVersions[]>, tab: ProjectTab) =>
  loaded.state === "ok"
    ? (loaded.data.find((t) => t.name === tab.name)?.versions ?? [])
    : [];

/** A tab's job as read; null while loading, after a failure, or with none. */
export const queuedOf = (
  loaded: Loaded<TabVersions[]>,
  tab: ProjectTab,
): Queued | null =>
  loaded.state === "ok"
    ? (loaded.data.find((t) => t.name === tab.name)?.queued ?? null)
    : null;
