// The theme's tokens as the Monitor page's canvas and measurements need
// them: read from the page, since uPlot draws on a canvas, not with CSS.
import { useEffect, useState } from "react";

/** A token's value as the page now has it, as "--text-2xs". */
export const cssVar = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** A colour token's value, as "series-1". */
export const colour = (name: string) => cssVar(`--color-${name}`);

/** A token in pixels, as "--spacing-chart"; 0 when the page has none. */
export function px(name: string): number {
  const v = parseFloat(cssVar(name));
  return Number.isFinite(v) ? v : 0;
}

/** The colour template's ID, which changes whenever the template does. */
export function useTemplate(): string {
  const [template, setTemplate] = useState(
    () => document.documentElement.dataset["template"] ?? "",
  );
  useEffect(() => {
    const html = document.documentElement;
    const observer = new MutationObserver(() => {
      setTemplate(html.dataset["template"] ?? "");
    });
    observer.observe(html, { attributeFilter: ["data-template"] });
    return () => {
      observer.disconnect();
    };
  }, []);
  return template;
}
