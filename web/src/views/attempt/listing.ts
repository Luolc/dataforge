// The listing of an attempt's item, which the heading's tags and its menu of
// the item's attempts both read, once.
import { urls, useJson, useRefresh } from "../../api/client";
import { attemptRows, type Project } from "../../api/types";

/** The item an attempt ID names: its first item.depth components. */
export const itemOf = (project: Project, attempt: string) =>
  attempt.split("/").slice(0, project.item.depth).join("/");

/** The rows of the attempt's item, read again as the page reads again. */
export function useListing(project: Project, attempt: string) {
  return useJson(
    urls.attempts(project.id, itemOf(project, attempt)),
    attemptRows,
    useRefresh(),
  );
}
