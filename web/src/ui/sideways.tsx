// A box that scrolls sideways on its own, such as a table wider than the page.
// Its scrollbar is at its foot, out of view under a long table, so while the
// box is wider than it shows, a line above it says so and scrolls it.
import { ArrowLeft, ArrowRight } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

interface Edges {
  /** Whether what the box holds is wider than the box. */
  wide: boolean;
  atStart: boolean;
  atEnd: boolean;
}

const button =
  "inline-flex target items-center justify-center rounded-row border border-line px-1.5 text-fg-2 hover:bg-hover disabled:cursor-default disabled:text-fg-3 disabled:hover:bg-transparent";

/** The box, and above it, while it is wider than it shows, a line to scroll it. */
export function Sideways({
  label,
  children,
}: {
  /** What the box holds, as "the table". */
  label: string;
  children: ReactNode;
}) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState<Edges>({
    wide: false,
    atStart: true,
    atEnd: true,
  });
  useEffect(() => {
    const box = ref.current;
    if (box === null) return;
    const measure = () => {
      const max = box.scrollWidth - box.clientWidth;
      const next = {
        wide: max > 1,
        atStart: box.scrollLeft <= 1,
        atEnd: box.scrollLeft >= max - 1,
      };
      setEdges((last) =>
        last.wide === next.wide &&
        last.atStart === next.atStart &&
        last.atEnd === next.atEnd
          ? last
          : next,
      );
    };
    measure();
    box.addEventListener("scroll", measure, { passive: true });
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(measure);
    observer?.observe(box);
    const inner = box.firstElementChild;
    if (inner !== null) observer?.observe(inner);
    return () => {
      box.removeEventListener("scroll", measure);
      observer?.disconnect();
    };
  }, []);
  // A step of most of what shows, at once: nothing on the page moves on its
  // own (R243).
  const step = (sign: number) => {
    const box = ref.current;
    if (box !== null) box.scrollLeft += sign * box.clientWidth * 0.8;
  };
  return (
    <>
      {edges.wide && (
        <p className="flex items-center justify-end gap-1.5 text-xs text-fg-3">
          {`${label[0]?.toUpperCase() ?? ""}${label.slice(1)} is wider than the page; scroll it sideways.`}
          <button
            type="button"
            aria-controls={id}
            aria-label={`Scroll ${label} left`}
            disabled={edges.atStart}
            onClick={() => {
              step(-1);
            }}
            className={button}
          >
            <ArrowLeft className="size-3.5" aria-hidden />
          </button>
          <button
            type="button"
            aria-controls={id}
            aria-label={`Scroll ${label} right`}
            disabled={edges.atEnd}
            onClick={() => {
              step(1);
            }}
            className={button}
          >
            <ArrowRight className="size-3.5" aria-hidden />
          </button>
        </p>
      )}
      <div id={id} ref={ref} className="overflow-x-auto">
        {children}
      </div>
    </>
  );
}
