// Cutting a long text that a control shows whole (L46).

/** The most characters of a principle shown before its Show all. */
export const PRINCIPLE_CUT = 160;

/**
 * A text cut to at most max characters at the last space before them, with
 * an ellipsis; null when it is no longer than max.
 */
export function cutAt(text: string, max: number): string | null {
  if (text.length <= max) return null;
  const space = text.lastIndexOf(" ", max);
  return `${text.slice(0, space > 0 ? space : max).trimEnd()}…`;
}
