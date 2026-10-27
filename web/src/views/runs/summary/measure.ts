// How wide a card's text is drawn, so that its lines break before they
// overflow it. In the browser it is measured on a canvas in the page's own
// font stack, which draws in whatever font the page's text draws in at the
// time; where there is no canvas, every character counts as wide as the
// widest the font has, so that nothing overflows.

import { useEffect, useState, useSyncExternalStore } from "react";

/** A card's title, the lines under it, and a line of an edge's label. */
export type Font = "title" | "sub" | "label";

/** How wide a text is drawn in a font, in px. */
export type Measure = (text: string, font: Font) => number;

/**
 * The theme's tokens of each font's weight and size, as `Graph.tsx` draws
 * them: a title `text-md font-semibold`, the lines under it `text-xs` in
 * the page's own weight, and an edge's label `text-xs`, semibold where the
 * attempt took it, as wide as it is semibold.
 */
const TOKENS: Record<Font, [weight: string, size: string]> = {
  title: ["--font-weight-semibold", "--text-md"],
  sub: ["--font-weight-normal", "--text-xs"],
  label: ["--font-weight-semibold", "--text-xs"],
};

/** The widest a character of each font is drawn (Œ in Mona Sans). */
const WIDEST: Record<Font, number> = { title: 15.1, sub: 12.8, label: 13 };

/** Every character as wide as the widest of its font. */
export const widest: Measure = (text, font) =>
  Array.from(text).length * WIDEST[font];

/**
 * A measure on a canvas in the page's sans-serif font, at the weights and
 * sizes of the theme's tokens, each text measured once; widest where the
 * browser has no OffscreenCanvas or the page none of those tokens.
 */
export function canvasMeasure(): Measure {
  if (typeof OffscreenCanvas === "undefined") return widest;
  const context = new OffscreenCanvas(1, 1).getContext("2d");
  if (context === null) return widest;
  const style = getComputedStyle(document.documentElement);
  const token = (name: string) => style.getPropertyValue(name).trim();
  const names = ["--font-sans", ...Object.values(TOKENS).flat()];
  if (names.some((name) => token(name) === "")) return widest;
  const fontOf = (font: Font) =>
    `${TOKENS[font].map(token).join(" ")} ${token("--font-sans")}`;
  const fonts: Record<Font, string> = {
    title: fontOf("title"),
    sub: fontOf("sub"),
    label: fontOf("label"),
  };
  const seen = new Map<string, number>();
  return (text, font) => {
    const key = `${font}\0${text}`;
    const known = seen.get(key);
    if (known !== undefined) return known;
    context.font = fonts[font];
    // The lines under a title set their digits tabular, which a canvas
    // cannot: every digit is measured as a 0, the widest of them; so is a
    // label's, which keeps it no narrower than it draws.
    const width = context.measureText(
      font === "title" ? text : text.replace(/\d/g, "0"),
    ).width;
    seen.set(key, width);
    return width;
  };
}

/**
 * The least side of a link on the graph's labels, 24px, 44px for a coarse
 * pointer (R246); a line that holds links is as tall as that. They are the
 * values of the tokens --spacing-target and --spacing-target-coarse in
 * theme.css, which the layout needs as numbers before anything is drawn.
 */
export const TARGET = 24;
const TARGET_COARSE = 44;

const COARSE = "(pointer: coarse)";
const coarseQuery = () =>
  typeof window === "undefined" || typeof window.matchMedia !== "function"
    ? null
    : window.matchMedia(COARSE);

function onCoarseChange(change: () => void): () => void {
  const query = coarseQuery();
  query?.addEventListener("change", change);
  return () => {
    query?.removeEventListener("change", change);
  };
}

/**
 * The least side of a link drawn on the graph: TARGET_COARSE where the
 * pointer is coarse, a touch screen, TARGET elsewhere.
 */
export function useTarget(): number {
  const coarse = useSyncExternalStore(
    onCoarseChange,
    () => coarseQuery()?.matches ?? false,
    () => false,
  );
  return coarse ? TARGET_COARSE : TARGET;
}

/**
 * The page's measure, new each time a font finishes loading, so that the
 * graph is laid out again in the font its text then draws in.
 */
export function useMeasure(): Measure {
  const [measure, setMeasure] = useState(canvasMeasure);
  useEffect(() => {
    if (typeof document === "undefined" || !("fonts" in document)) return;
    const fonts = document.fonts;
    const again = () => {
      setMeasure(() => canvasMeasure());
    };
    fonts.addEventListener("loadingdone", again);
    return () => {
      fonts.removeEventListener("loadingdone", again);
    };
  }, []);
  return measure;
}
