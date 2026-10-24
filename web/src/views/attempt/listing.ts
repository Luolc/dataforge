// The project's listing of attempts, which the heading's tags and its menu of
// the item's attempts both read, once.
import { urls, useJson, useRefresh } from "../../api/client";
import { attemptRows, type Project } from "../../api/types";

/** The listing, read again as the page reads again. */
export function useListing(project: Project) {
  return useJson(urls.attempts(project.id), attemptRows, useRefresh());
}
