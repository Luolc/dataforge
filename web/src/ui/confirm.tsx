// A button whose act takes a second click: the first asks whether to go on.
import type { ReactNode } from "react";

import { useAsk } from "./ask";

/** The look of a small bordered button. */
export const control =
  "target inline-flex items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs font-medium hover:bg-hover disabled:cursor-not-allowed disabled:border-line-subtle disabled:text-fg-3 disabled:hover:bg-transparent";

type Ask = ReturnType<typeof useAsk>;

/** question, with a button named yes, which runs onYes, and Keep. */
export function Question({
  ask,
  question,
  yes,
  onYes,
}: {
  ask: Ask;
  question: string;
  yes: string;
  onYes: () => Promise<void>;
}) {
  return (
    <span className="inline-flex flex-wrap items-center gap-2 text-xs">
      <span>{question}</span>
      <button
        type="button"
        className={control}
        disabled={ask.busy}
        onClick={() => {
          ask.run(onYes);
        }}
      >
        {yes}
      </button>
      <button
        type="button"
        className={control}
        disabled={ask.busy}
        onClick={ask.keep}
      >
        Keep
      </button>
    </span>
  );
}

/**
 * children labels the button; once clicked, the question shows in its place
 * (Question).
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
  const ask = useAsk();
  if (ask.asking)
    return <Question ask={ask} question={question} yes={yes} onYes={onYes} />;
  return (
    <button
      type="button"
      className={control}
      disabled={disabled}
      aria-describedby={describedBy}
      onClick={ask.ask}
    >
      {children}
    </button>
  );
}
