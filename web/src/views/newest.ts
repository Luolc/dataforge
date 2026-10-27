// Which directory gives a deliverable's path in place of the record's, as
// the attempt page read it from /newest, for the views that show the
// deliverable; a page with no such read, as the share site's, has none.
import { createContext, use } from "react";

import { fromARun, type Placed } from "./source";

export type NewestRead =
  | { state: "none" }
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ok"; dir: string };

/** What /newest says of a deliverable, by its name. */
export const NewestContext = createContext<(name: string) => NewestRead>(
  () => ({ state: "none" }),
);

/** What /newest says of the deliverable of a name. */
export const useNewest = (name: string): NewestRead => use(NewestContext)(name);

/** Whether a deliverable's card says where it came from: a run or a directory. */
export function useHasSource(deliverable: Placed): boolean {
  const newest = useNewest(deliverable.name);
  return fromARun(deliverable) || newest.state === "ok";
}
