// Whether a person is being asked to confirm an act, for Confirm and Question.
import { useState } from "react";

/** Whether a question is out, and whether its act runs. */
export function useAsk() {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  return {
    asking,
    busy,
    ask: () => {
      setAsking(true);
    },
    keep: () => {
      setAsking(false);
    },
    /** Run act; while it runs, the question's buttons stay disabled. */
    run: (act: () => Promise<void>) => {
      setBusy(true);
      void act().finally(() => {
        setBusy(false);
        setAsking(false);
      });
    },
  };
}
