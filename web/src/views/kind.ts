import type {
  AttemptRecord,
  Deliverable,
  LiveInfo,
  Project,
} from "../api/types";

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
