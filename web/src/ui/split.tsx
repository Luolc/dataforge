// A column beside what it lists, as wide as a reader drags or keys the
// divider between them. Its width comes from the theme's tokens of a name:
// --spacing-<name> at first, never under --spacing-<name>-min nor over
// --spacing-<name>-max or half the split, and a key moves it by
// --spacing-<name>-step. The width a reader sets is kept in localStorage and
// held to those bounds again wherever the split is drawn.
import {
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

/** A spacing token in pixels, as "--spacing-tree"; 0 when the page has none. */
function token(name: string): number {
  const v = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue(name),
  );
  return Number.isFinite(v) ? v : 0;
}

interface Bounds {
  first: number;
  least: number;
  most: number;
  step: number;
}

/** The bounds of a split of the name, as wide as width (0: not laid out). */
function boundsOf(name: string, width: number): Bounds {
  const least = token(`--spacing-${name}-min`);
  const most = Math.min(
    token(`--spacing-${name}-max`),
    width > 0 ? width / 2 : Infinity,
  );
  return {
    first: token(`--spacing-${name}`),
    least,
    most: Math.max(least, most),
    step: token(`--spacing-${name}-step`),
  };
}

const within = (v: number, b: Bounds) =>
  Math.round(Math.min(Math.max(v, b.least), b.most));

/** The width kept under key, or null when there is none or it is no width. */
function saved(key: string): number | null {
  try {
    const v = localStorage.getItem(key);
    return v !== null && /^\d+$/.test(v) ? Number(v) : null;
  } catch {
    return null;
  }
}

function save(key: string, width: number) {
  try {
    localStorage.setItem(key, String(width));
  } catch {
    // Private mode: the width lasts for this page only.
  }
}

// The divider draws no line of its own over the column's border but under a
// hovering pointer, a drag or the focus; it takes a target's width centred
// on that border, and moves with no transition.
const divider =
  "absolute inset-y-0 w-target -translate-x-1/2 cursor-col-resize touch-none outline-none pointer-coarse:w-target-coarse before:absolute before:inset-y-0 before:left-1/2 before:w-0.5 before:-translate-x-1/2 hover:before:bg-accent focus-visible:before:bg-focus data-dragging:before:bg-accent";

export function Split({
  name,
  storageKey,
  label,
  sideClass,
  side,
  children,
}: {
  /** The name of the width's tokens, as "tree" for --spacing-tree. */
  name: string;
  /** Where the width a reader sets is kept in localStorage. */
  storageKey: string;
  /** The divider's name, as a reader announces it. */
  label: string;
  /** The column's own classes, as its surface and border. */
  sideClass: string;
  /** The column, at the left. */
  side: ReactNode;
  /** What is beside it. */
  children: ReactNode;
}) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [chosen, setChosen] = useState(() => saved(storageKey));
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{
    x: number;
    from: number;
    to: number;
    bounds: Bounds;
  }>(null);

  useLayoutEffect(() => {
    const box = ref.current;
    if (box === null) return;
    const measure = () => {
      setWidth(box.getBoundingClientRect().width);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    return () => {
      observer.disconnect();
    };
  }, []);

  const bounds = boundsOf(name, width);
  const now = within(chosen ?? bounds.first, bounds);
  const choose = (v: number) => {
    setChosen(v);
    save(storageKey, v);
  };

  // A drag ends where it got to, whether the pointer lets go or the page
  // takes it away; where it got to is kept with the drag, since the release
  // may come before the render of the last move.
  const end = () => {
    const d = dragRef.current;
    if (d === null) return;
    dragRef.current = null;
    setDragging(false);
    if (d.to !== d.from) save(storageKey, d.to);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    // No text is selected as the pointer moves, and the divider takes the
    // focus as it would on a click.
    e.preventDefault();
    e.currentTarget.focus();
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { x: e.clientX, from: now, to: now, bounds };
    setDragging(true);
  };
  const onPointerMove = (e: PointerEvent) => {
    const d = dragRef.current;
    if (d === null) return;
    d.to = within(d.from + e.clientX - d.x, d.bounds);
    setChosen(d.to);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    const to =
      e.key === "ArrowLeft"
        ? now - bounds.step
        : e.key === "ArrowRight"
          ? now + bounds.step
          : e.key === "Home"
            ? bounds.least
            : e.key === "End"
              ? bounds.most
              : null;
    if (to === null) return;
    e.preventDefault();
    choose(within(to, bounds));
  };

  return (
    <div
      ref={ref}
      className="relative grid"
      style={{ gridTemplateColumns: `${String(now)}px minmax(0,1fr)` }}
    >
      <div id={id} className={sideClass}>
        {side}
      </div>
      {children}
      {/* eslint-disable-next-line jsx-a11y-x/no-noninteractive-element-interactions -- a separator that takes the focus is a widget (WAI-ARIA's window splitter) */}
      <div
        role="separator"
        // eslint-disable-next-line jsx-a11y-x/no-noninteractive-tabindex -- a keyboard moves the separator
        tabIndex={0}
        aria-orientation="vertical"
        aria-controls={id}
        aria-label={label}
        aria-valuenow={now}
        aria-valuemin={Math.round(bounds.least)}
        aria-valuemax={Math.round(bounds.most)}
        data-dragging={dragging ? "" : undefined}
        style={{ left: `${String(now)}px` }}
        className={divider}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={end}
        onLostPointerCapture={end}
        onKeyDown={onKeyDown}
      />
    </div>
  );
}
