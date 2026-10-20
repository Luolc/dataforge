import { ChevronDown, ChevronRight } from "lucide-react";
import { type ReactNode, useId, useState } from "react";

import type { Tone } from "../../../api/types";
import { toneGlyph } from "../../../ui/status";

const toneIcon: Record<Tone, string> = {
  good: "bg-good",
  bad: "bg-bad",
  warn: "bg-warn",
  neutral: "bg-neutral",
};

/**
 * One step of a run, folded or open. Its body is drawn only while it is
 * open, so a folded session loads nothing.
 */
export function Step({
  name,
  status,
  aside,
  open: startOpen,
  children,
}: {
  name: ReactNode;
  status: { label: string; tone: Tone };
  aside?: ReactNode;
  open: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(startOpen);
  const bodyId = useId();
  const Glyph = toneGlyph[status.tone];
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <section className="border-t border-line-subtle">
      <h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => {
            setOpen(!open);
          }}
          className="flex target w-full items-center gap-2 px-5 py-2.5 text-left hover:bg-hover"
        >
          <Chevron className="size-4 flex-none text-icon" aria-hidden />
          <span
            role="img"
            aria-label={status.label}
            title={status.label}
            className={`inline-grid size-4 flex-none place-items-center rounded-full ${toneIcon[status.tone]}`}
          >
            <Glyph
              className="size-2.5 text-on-tone"
              strokeWidth={3}
              aria-hidden
            />
          </span>
          <span className="min-w-0 flex-auto font-medium break-words">
            {name}
          </span>
          {aside !== undefined && (
            <span className="flex-none text-xs text-fg-3 tabular-nums">
              {aside}
            </span>
          )}
        </button>
      </h3>
      <div id={bodyId} hidden={!open} className="px-5 pt-1 pb-4 pl-15">
        {open && children}
      </div>
    </section>
  );
}
