import type {
  AttemptRecord,
  Deliverable,
  LiveInfo,
  Project,
} from "../api/types";
import { NOT_RUN } from "../ui/status";

/** The project file's entry for a deliverable, by its name, if it has one. */
export function declared(
  project: Pick<Project, "deliverables">,
  name: string,
): NonNullable<Project["deliverables"]>[number] | undefined {
  return project.deliverables?.find((d) => d.name === name);
}

/** Not run, with why for a deliverable not reached, when there is a why. */
export function notRun(deliverable: Pick<Deliverable, "reason">): string {
  const { reason } = deliverable;
  return reason === null || reason === "" ? NOT_RUN : `${NOT_RUN}: ${reason}`;
}

/** What a deliverable kind's view gets: one deliverable and its record. */
export interface DeliverableProps {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  deliverable: Deliverable;
  /** Whether the attempt runs now; null or absent while it is not known. */
  live?: LiveInfo | null;
}

/**
 * Deliverables of a kind in the order their tab stacks them: those the
 * attempt reached, then the others, each in their own order.
 */
export function reachedFirst(items: Deliverable[]): Deliverable[] {
  const reached = (item: Deliverable) => item.path !== null;
  return [...items.filter(reached), ...items.filter((item) => !reached(item))];
}

/**
 * The deliverable of a kind that its tab is titled and counted by and opens
 * on: the first the attempt reached, else the one the project marks
 * default, else the first.
 */
export function leading(
  project: Project,
  items: Deliverable[],
): Deliverable | undefined {
  const reached = items.find((item) => item.path !== null);
  if (reached !== undefined) return reached;
  const marked = (project.deliverables ?? []).filter((d) => d.default === true);
  return (
    items.find((item) => marked.some((d) => d.name === item.name)) ?? items[0]
  );
}

/** The title of a kind's tab: the project's for the kind, else its leading one's. */
export function kindTitle(
  project: Project,
  kind: string,
  items: Deliverable[],
): string {
  return project.kinds?.[kind]?.title ?? leading(project, items)?.title ?? kind;
}
