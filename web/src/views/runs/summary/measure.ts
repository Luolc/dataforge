// How wide a card's text is drawn, so that its lines break before they
// overflow it. In the browser it is measured on a canvas in the page's own
// font stack, which draws in whatever font the page's text draws in at the
// time; where there is no canvas, every character counts as wide as the
// widest the font has, so that nothing overflows.

import { useEffect, useState } from "react";

/** A card's title, and the lines under it. */
export type Font = "title" | "sub";

/** How wide a text is drawn in a font, in px. */
export type Measure = (text: string, font: Font) => number;

/** The weight and size of each font, as `Graph.tsx` draws them. */
const FONTS: Record<Font, string> = {
  title: "600 14px",
  sub: "400 12px",
};

/** The widest a character of each font is drawn (Œ in Mona Sans). */
const WIDEST: Record<Font, number> = { title: 15.1, sub: 12.8 };

/** Every character as wide as the widest of its font. */
export const widest: Measure = (text, font) =>
  Array.from(text).length * WIDEST[font];

/**
 * A measure on a canvas in the page's sans-serif font, each text measured
 * once; widest where the browser has no OffscreenCanvas.
 */
export function canvasMeasure(): Measure {
  if (typeof OffscreenCanvas === "undefined") return widest;
  const context = new OffscreenCanvas(1, 1).getContext("2d");
  if (context === null) return widest;
  const stack = getComputedStyle(document.documentElement)
    .getPropertyValue("--font-sans")
    .trim();
  const family = stack === "" ? "sans-serif" : stack;
  const seen = new Map<string, number>();
  return (text, font) => {
    const key = `${font}\0${text}`;
    const known = seen.get(key);
    if (known !== undefined) return known;
    context.font = `${FONTS[font]} ${family}`;
    // The lines under a title set their digits tabular, which a canvas
    // cannot: every digit is measured as a 0, the widest of them.
    const width = context.measureText(
      font === "sub" ? text.replace(/\d/g, "0") : text,
    ).width;
    seen.set(key, width);
    return width;
  };
}

/**
 * The page's measure, new each time a font finishes loading, so that the
 * graph is laid out again in the font its text then draws in.
 */
export function useMeasure(): Measure {
  const [measure, setMeasure] = useState(canvasMeasure);
  useEffect(() => {
    if (typeof document === "undefined" || !("fonts" in document)) return;
    const again = () => {
      setMeasure(() => canvasMeasure());
    };
    document.fonts.addEventListener("loadingdone", again);
    return () => {
      document.fonts.removeEventListener("loadingdone", again);
    };
  }, []);
  return measure;
}
