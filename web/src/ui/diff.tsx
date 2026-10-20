// A unified diff coloured by line, for any view that shows one.
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
export function DiffView({ text }: { text: string }) {
  const lines = (text.endsWith("\n") ? text.slice(0, -1) : text).split("\n");
  const kinds = diffKinds(lines);
  return (
    <div className="overflow-auto bg-code-bg py-2.5 font-mono text-code tracking-normal">
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
