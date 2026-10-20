// A button whose act takes a second click: the first asks, in place, whether
// to go on.
import { type ReactNode, useState } from "react";

/** The look of a small bordered button. */
export const control =
  "target inline-flex items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs font-medium hover:bg-hover disabled:cursor-not-allowed disabled:border-line-subtle disabled:text-fg-3 disabled:hover:bg-transparent";

/**
 * children labels the button; once clicked, question shows in its place with
 * a button named yes, which calls onYes, and Keep, which goes back. While
 * onYes runs, both stay disabled.
 */
export function Confirm({
  children,
  question,
  yes,
  onYes,
  disabled = false,
  describedBy,
}: {
  children: ReactNode;
  question: string;
  yes: string;
  onYes: () => Promise<void>;
  disabled?: boolean;
  describedBy?: string;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!asking)
    return (
      <button
        type="button"
        className={control}
        disabled={disabled}
        aria-describedby={describedBy}
        onClick={() => {
          setAsking(true);
        }}
      >
        {children}
      </button>
    );
  return (
    <span className="inline-flex flex-wrap items-center gap-2 text-xs">
      <span>{question}</span>
      <button
        type="button"
        className={control}
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void onYes().finally(() => {
            setBusy(false);
            setAsking(false);
          });
        }}
      >
        {yes}
      </button>
      <button
        type="button"
        className={control}
        disabled={busy}
        onClick={() => {
          setAsking(false);
        }}
      >
        Keep
      </button>
    </span>
  );
}
