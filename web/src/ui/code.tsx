// Read-only source with syntax highlighting, chosen by a file's extension.
import type { Root, RootContent } from "hast";
import type { ReactNode } from "react";

import { languageOf, lowlight } from "./language";

/** Text as highlighted code; language null shows it plain. */
export function Highlighted({
  text,
  language,
}: {
  text: string;
  language: string | null;
}) {
  if (language === null) return <>{text}</>;
  return <>{toReact(lowlight.highlight(language, text))}</>;
}

// lowlight's tree holds only text and spans with class names, never HTML.
function toReact(node: Root | RootContent, key?: number): ReactNode {
  if (node.type === "text") return node.value;
  if (node.type !== "root" && node.type !== "element") return null;
  const children = node.children.map((child, i) => toReact(child, i));
  if (node.type === "root") return children;
  const names = node.properties.className;
  const className = Array.isArray(names) ? names.join(" ") : undefined;
  return (
    <span key={key} className={className}>
      {children}
    </span>
  );
}

/** A file's text with line numbers, highlighted by its name. */
export function CodeView({ name, text }: { name: string; text: string }) {
  const lines = text.endsWith("\n") ? text.slice(0, -1) : text;
  const count = lines.split("\n").length;
  return (
    <div className="flex overflow-auto py-2.5 font-mono text-code tracking-normal">
      <pre
        aria-hidden
        className="flex-none pr-4 pl-5 text-right text-faint select-none"
      >
        {Array.from({ length: count }, (_, i) => i + 1).join("\n")}
      </pre>
      <pre className="pr-5">
        <code>
          <Highlighted text={lines} language={languageOf(name)} />
        </code>
      </pre>
    </div>
  );
}
