// How an act a person asked for went, said where it takes the focus.
import { type ReactNode, useEffect, useRef } from "react";

import { toneGlyph, toneText } from "./status";

/** What an act came to: text, and whether it went as asked. */
export interface Said {
  ok: boolean;
  text: string;
}

/**
 * A status region, always on the page so that a reader announces what comes
 * into it; each new said takes the focus, which the button that acted may
 * have lost when it went or was disabled. after follows a said that is ok.
 */
export function SaidLine({
  said,
  after,
}: {
  said: Said | null;
  after?: ReactNode;
}) {
  const lineRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (said !== null) lineRef.current?.focus();
  }, [said]);
  const Bad = toneGlyph.bad;
  return (
    <div ref={lineRef} role="status" tabIndex={-1} className="text-xs">
      {said !== null &&
        (said.ok ? (
          <span>
            {said.text}
            {after !== undefined && <> {after}</>}
          </span>
        ) : (
          <span className={`inline-flex items-center gap-1.5 ${toneText.bad}`}>
            <Bad className="size-3.5 flex-none" aria-hidden />
            {said.text}
          </span>
        ))}
    </div>
  );
}
