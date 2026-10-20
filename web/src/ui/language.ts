// The highlighter, and which of its languages a file or a code fence is in.
import { common, createLowlight } from "lowlight";

export const lowlight = createLowlight(common);

const byExtension: Record<string, string> = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  ts: "typescript",
  tsx: "typescript",
  json: "json",
  jsonl: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  sh: "bash",
  md: "markdown",
  diff: "diff",
  patch: "diff",
  c: "c",
  h: "c",
  cc: "cpp",
  cpp: "cpp",
  go: "go",
  rs: "rust",
  java: "java",
  rb: "ruby",
  sql: "sql",
  xml: "xml",
  html: "xml",
  css: "css",
};

/** The highlighter's language for a file name, or null for plain text. */
export function languageOf(name: string): string | null {
  if (/^dockerfile$/i.test(name)) return "dockerfile";
  const extension = /\.([^./]+)$/.exec(name)?.[1]?.toLowerCase();
  const language = extension === undefined ? undefined : byExtension[extension];
  return language !== undefined && lowlight.registered(language)
    ? language
    : null;
}

/** The highlighter's language for a code fence's tag, or null. */
export function fenceLanguage(tag: string): string | null {
  return lowlight.registered(tag) ? tag : languageOf(`x.${tag}`);
}
