// A read-only preview of one file: Markdown rendered, a diff coloured by line,
// other text highlighted by its extension, and anything else by its size.
import { Download } from "lucide-react";

import { useFileSource } from "../../api/client";
import { Note } from "../../ui/card";
import { CodeView } from "../../ui/code";
import { DiffView } from "../../ui/diff";
import { fileTypeOf } from "../../ui/file-kind";
import { Markdown } from "../../ui/markdown";
import { type FileContent, size, tooLarge, useFile } from "./file";

const lines = (n: number) => `${n.toString()} line${n === 1 ? "" : "s"}`;

const lineCount = (text: string) =>
  (text.endsWith("\n") ? text.slice(0, -1) : text).split("\n").length;

/**
 * The file at path, under a header with its name, its lines and size and a
 * link that downloads it, when the source gives one and serves the file.
 */
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
  const source = useFileSource();
  const loaded = useFile(project, attempt, path);
  const content = loaded.state === "ok" ? loaded.data : null;
  const served = content?.kind === "text" || content?.kind === "binary";
  return (
    <div className="min-w-0">
      <header className="flex flex-wrap items-center justify-between gap-x-3 border-b border-line bg-head px-4 py-1">
        <h3 className="py-1.5 font-mono text-xs break-all">{name}</h3>
        <span className="flex flex-none items-center gap-3 text-xs text-fg-3 tabular-nums">
          {content?.kind === "text" &&
            `${lines(lineCount(content.text))} · ${size(content.size)}`}
          {content?.kind === "binary" && size(content.size)}
          {served && source.href && (
            <a
              href={source.href(project, attempt, path)}
              download={path.split("/").at(-1)}
              className="inline-flex target items-center gap-1 link hover:underline"
            >
              <Download className="size-3.5 flex-none" aria-hidden />
              Download
            </a>
          )}
        </span>
      </header>
      {loaded.state === "loading" ? (
        <Note>Loading…</Note>
      ) : loaded.state === "error" ? (
        <Note>{loaded.message}</Note>
      ) : (
        <Body
          name={name}
          path={path}
          content={loaded.data}
          downloadable={source.href !== undefined}
        />
      )}
    </div>
  );
}

function Body({
  name,
  path,
  content,
  downloadable,
}: {
  name: string;
  path: string;
  content: FileContent;
  downloadable: boolean;
}) {
  if (content.kind === "too-large") {
    return <Note>{tooLarge(content.reason, path)}</Note>;
  }
  if (content.kind === "missing") return <Note>{content.reason}</Note>;
  if (content.kind === "binary") {
    const how = downloadable ? " Download it with the link above." : "";
    return (
      <Note>{`Not shown: not UTF-8 text, ${size(content.size)}.${how}`}</Note>
    );
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
