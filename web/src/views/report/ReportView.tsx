import { Placeholder } from "../DeliverableCard";
import type { KindProps } from "../kind";

/** The report view, for deliverables of kind report. */
export function ReportView({ items }: KindProps) {
  return <Placeholder what="report view" items={items} />;
}
