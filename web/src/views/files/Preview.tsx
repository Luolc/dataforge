// A read-only preview of one file: Markdown rendered, a diff coloured by line,
// other text highlighted by its extension, and anything else by its size.
import { Note } from "../../ui/card";
import { CodeView } from "../../ui/code";
import { DiffView } from "../../ui/diff";
import { fileTypeOf } from "../../ui/file-kind";
import { Markdown } from "../../ui/markdown";
import { type FileContent, size, useFile } from "./file";

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
  if (content.kind === "missing") return <Note>{content.reason}</Note>;
  if (content.kind === "binary") {
    return <Note>{`Not shown: not UTF-8 text, ${size(content.size)}.`}</Note>;
  }
  const { kind } = fileTypeOf(name);
  if (kind === "markdown") {
    return (
      <div className="px-5 py-4">
        <Markdown text={content.text} />
      </div>
    );
  }
  if (kind === "diff") return <DiffView text={content.text} />;
  return <CodeView name={name} text={content.text} />;
}
