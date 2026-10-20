// A read-only preview of one file: Markdown rendered, a diff coloured by line,
// other text highlighted by its extension, and anything else by its size.
import { Note } from "../../ui/card";
import { CodeView } from "../../ui/code";
import { Markdown } from "../../ui/markdown";
import { type FileContent, size, useFile } from "./file";

const isMarkdown = (name: string) => /\.(md|markdown)$/i.test(name);
const isDiff = (name: string) => /\.(diff|patch)$/i.test(name);

const lineCount = (text: string) =>
  (text.endsWith("\n") ? text.slice(0, -1) : text).split("\n").length;

/** The file at path, under a header with its name, lines and size. */
export function Preview({
  project,
  attempt,
  path,
  name,
}: {
  project: string;
  attempt: string;
  path: string;
  /** The path as the header shows it. */
  name: string;
}) {
  const loaded = useFile(project, attempt, path);
  const content = loaded.state === "ok" ? loaded.data : null;
  return (
    <div className="min-w-0">
      <header className="flex items-baseline justify-between gap-3 border-b border-line px-4 py-2.5">
        <h3 className="font-mono text-xs break-all">{name}</h3>
        {content?.kind === "text" && (
          <span className="flex-none text-xs text-fg-3 tabular-nums">
            {`${lineCount(content.text).toString()} lines · ${size(content.size)}`}
          </span>
        )}
      </header>
      {loaded.state === "loading" ? (
        <Note>Loading…</Note>
      ) : loaded.state === "error" ? (
        <Note>{loaded.message}</Note>
      ) : (
        <Body name={name} content={loaded.data} />
      )}
    </div>
  );
}

function Body({ name, content }: { name: string; content: FileContent }) {
  if (content.kind === "too-large") {
    return <Note>{`Not shown: ${content.reason}`}</Note>;
  }
  if (content.kind === "binary") {
    return <Note>{`Not shown: not UTF-8 text, ${size(content.size)}.`}</Note>;
  }
  if (isMarkdown(name)) {
    return (
      <div className="px-5 py-4">
        <Markdown text={content.text} />
      </div>
    );
  }
  if (isDiff(name)) return <DiffView text={content.text} />;
  return <CodeView name={name} text={content.text} />;
}

type LineKind = "add" | "remove" | "hunk" | "head" | "context";

/**
 * Each line's kind. A hunk runs for as many lines as its "@@" header counts,
 * so inside one a line is told by its first character alone, even a removed
 * "--" or an added "++"; outside one, a line is a file header.
 */
function diffKinds(lines: string[]): LineKind[] {
  let old = 0;
  let added = 0;
  return lines.map((line) => {
    if (line.startsWith("\\")) return "context";
    if (old > 0 || added > 0) {
      if (line.startsWith("+")) {
        added--;
        return "add";
      }
      if (line.startsWith("-")) {
        old--;
        return "remove";
      }
      old--;
      added--;
      return "context";
    }
    const hunk = /^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@/.exec(line);
    if (hunk === null) return "head";
    old = Number(hunk[1] ?? 1);
    added = Number(hunk[2] ?? 1);
    return "hunk";
  });
}

const lineClass = {
  add: { line: "bg-good-bg", text: "text-fg" },
  remove: { line: "bg-bad-bg", text: "text-fg" },
  hunk: { line: "bg-hover", text: "text-fg-3" },
  head: { line: "", text: "font-medium text-fg-2" },
  context: { line: "", text: "text-fg" },
};

/** A unified diff, each line with its number and a background by its kind. */
function DiffView({ text }: { text: string }) {
  const lines = (text.endsWith("\n") ? text.slice(0, -1) : text).split("\n");
  const kinds = diffKinds(lines);
  return (
    <div className="overflow-auto py-2.5 font-mono text-code tracking-normal">
      <pre className="min-w-fit">
        {lines.map((line, i) => {
          const kind = kinds[i] ?? "context";
          return (
            <div
              key={i}
              data-line={kind}
              className={`flex ${lineClass[kind].line}`}
            >
              <span
                aria-hidden
                className="w-12 flex-none pr-4 text-right text-faint select-none"
              >
                {i + 1}
              </span>
              <code className={`pr-5 ${lineClass[kind].text}`}>
                {line === "" ? " " : line}
              </code>
            </div>
          );
        })}
      </pre>
    </div>
  );
}
