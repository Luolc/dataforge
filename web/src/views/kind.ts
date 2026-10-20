import type { AttemptRecord, Deliverable, Project } from "../api/types";

/** What a deliverable kind's view gets: every deliverable of its kind. */
export interface KindProps {
  project: Project;
  attempt: string;
  record: AttemptRecord;
  items: Deliverable[];
}
