// Whether a person is being asked to confirm an act, for Confirm and Question.
import { useId, useRef, useState } from "react";

/**
 * Whether a question is out, and whether its act runs. triggerRef is the button
 * that asks, which takes the focus back when the person keeps things as they
 * are; id is the question's.
 */
export function useAsk() {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const id = useId();
  return {
    asking,
    busy,
    triggerRef,
    id,
    ask: () => {
      setAsking(true);
    },
    keep: () => {
      setAsking(false);
      triggerRef.current?.focus();
    },
    /**
     * Run act; while it runs, the question's buttons stay disabled. act says
     * how it went and takes the focus there.
     */
    run: (act: () => Promise<void>) => {
      setBusy(true);
      void act().finally(() => {
        setBusy(false);
        setAsking(false);
      });
    },
  };
}

/** The props of the button that asks: it says whether the question is out. */
export const triggerProps = (ask: ReturnType<typeof useAsk>) => ({
  ref: ask.triggerRef,
  "aria-expanded": ask.asking,
  "aria-controls": ask.id,
  onClick: ask.ask,
});
