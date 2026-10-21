// A button whose act takes a second click: the first asks whether to go on.
import { type ReactNode, useEffect, useRef } from "react";

import { triggerProps, useAsk } from "./ask";

/** The look of a small bordered button. */
export const control =
  "target inline-flex items-center gap-1.5 rounded-row border border-chip-line px-2 text-xs font-medium hover:bg-hover disabled:cursor-not-allowed disabled:border-line-subtle disabled:text-fg-3 disabled:hover:bg-transparent";

type Ask = ReturnType<typeof useAsk>;

/**
 * question, with a button named yes, which runs onYes, and Keep. fields, when
 * given, sit between the question and yes, which stays disabled until ready;
 * the focus goes to the first field's input, else to yes, and Enter in an
 * input is yes.
 */
export function Question({
  ask,
  question,
  yes,
  onYes,
  fields,
  ready = true,
}: {
  ask: Ask;
  question: string;
  yes: string;
  onYes: () => Promise<void>;
  fields?: ReactNode;
  ready?: boolean;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  const yesRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const input = formRef.current?.querySelector("input");
    if (input) {
      input.focus();
      input.select();
    } else {
      yesRef.current?.focus();
    }
  }, []);
  return (
    <form
      ref={formRef}
      id={ask.id}
      className="inline-flex flex-wrap items-center gap-2 text-xs"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready && !ask.busy) ask.run(onYes);
      }}
    >
      <span>{question}</span>
      {fields}
      <button
        ref={yesRef}
        type="submit"
        className={control}
        disabled={ask.busy || !ready}
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
    </form>
  );
}

/**
 * children labels the button; once clicked, onAsk runs and the question shows
 * beside it (Question).
 */
export function Confirm({
  children,
  question,
  yes,
  onYes,
  fields,
  ready = true,
  onAsk,
  disabled = false,
  describedBy,
}: {
  children: ReactNode;
  question: string;
  yes: string;
  onYes: () => Promise<void>;
  fields?: ReactNode;
  ready?: boolean;
  onAsk?: () => void;
  disabled?: boolean;
  describedBy?: string;
}) {
  const ask = useAsk();
  return (
    <>
      <button
        type="button"
        className={control}
        disabled={disabled}
        aria-describedby={describedBy}
        {...triggerProps(ask)}
        onClick={() => {
          onAsk?.();
          ask.ask();
        }}
      >
        {children}
      </button>
      {ask.asking && (
        <Question
          ask={ask}
          question={question}
          yes={yes}
          onYes={onYes}
          fields={fields}
          ready={ready}
        />
      )}
    </>
  );
}
