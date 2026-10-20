// The highlighter, and which of its languages a file or a code fence is in.
import { common, createLowlight } from "lowlight";

import { fileTypeOf } from "./file-kind";

export const lowlight = createLowlight(common);

/** The highlighter's language for a file name, or null for plain text. */
export function languageOf(name: string): string | null {
  const { language } = fileTypeOf(name);
  return language !== null && lowlight.registered(language) ? language : null;
}

/** The highlighter's language for a code fence's tag, or null. */
export function fenceLanguage(tag: string): string | null {
  return lowlight.registered(tag) ? tag : languageOf(`x.${tag}`);
}
