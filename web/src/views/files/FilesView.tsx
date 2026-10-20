import { Placeholder } from "../DeliverableCard";
import type { KindProps } from "../kind";

/** The files view, for deliverables of kind files. */
export function FilesView({ items }: KindProps) {
  return <Placeholder what="files view" items={items} />;
}
