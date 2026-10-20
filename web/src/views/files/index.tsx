import { DeliverableCard } from "../DeliverableCard";
import type { DeliverableProps } from "../kind";

/** The files view, for a deliverable of kind files; not built yet. */
export default function FilesView({ deliverable }: DeliverableProps) {
  return (
    <DeliverableCard
      item={deliverable}
      note="The files view is not built yet."
    />
  );
}
