// Markdown from an attempt, which agents and the code under test wrote: it is
// untrusted. Raw HTML shows as text, an image is a link that loads nothing,
// and a link opens only on a click, in a new tab with no opener or referrer.
// react-markdown turns raw HTML into text unless a plugin such as rehype-raw
// is added; none may be.
import type { ReactNode } from "react";
import ReactMarkdown, { type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";

import { Highlighted } from "./code";
import { fenceLanguage } from "./language";

const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  img: ({ src, alt }) => {
    const url = typeof src === "string" ? src : "";
    return (
      <a href={url} target="_blank" rel="noopener noreferrer">
        {`image: ${alt !== undefined && alt !== "" ? alt : url}`}
      </a>
    );
  },
  code: ({ className, children }) => {
    const tag = /language-(\S+)/.exec(className ?? "")?.[1];
    const known = tag === undefined ? null : fenceLanguage(tag);
    return typeof children === "string" && known !== null ? (
      <code className={className}>
        <Highlighted text={children} language={known} />
      </code>
    ) : (
      <code className={className}>{children}</code>
    );
  },
};

/** Template variables to draw in place: what matches them, and how. */
export interface Variables {
  /** A global pattern; a match's name is its text without $, braces or spaces. */
  pattern: RegExp;
  draw: (name: string) => ReactNode;
}

interface MdNode {
  type: string;
  value?: string;
  lang?: string | null;
  children?: MdNode[];
  data?: { hName?: string; hProperties?: Record<string, unknown> };
}

const nameOf = (spelled: string) =>
  spelled.replace(/^\$\{?|\{\{|\}\}|\}$/g, "").trim();

/** text split at each variable into text and a node the HTML side draws as
 * <var>, whose one child is the variable's name; null when it has none. */
function split(text: string, pattern: RegExp): MdNode[] | null {
  const out: MdNode[] = [];
  let at = 0;
  for (const m of text.matchAll(pattern)) {
    if (m.index > at)
      out.push({ type: "text", value: text.slice(at, m.index) });
    out.push({
      type: "variable",
      data: { hName: "var" },
      children: [{ type: "text", value: nameOf(m[0]) }],
    });
    at = m.index + m[0].length;
  }
  if (out.length === 0) return null;
  if (at < text.length) out.push({ type: "text", value: text.slice(at) });
  return out;
}

// Splits text at each variable, in code too: a template puts a value in
// place wherever its variable is written, a code span or block included. A
// code holding one is drawn as <code> (in <pre> for a block) of those parts,
// with no highlighting.
function splitVariables(node: MdNode, pattern: RegExp) {
  if (node.children === undefined) return;
  node.children = node.children.flatMap((child): MdNode[] => {
    if (child.value === undefined) {
      splitVariables(child, pattern);
      return [child];
    }
    const parts = split(child.value, pattern);
    if (parts === null) return [child];
    if (child.type === "text") return parts;
    // Kept whole on a line where it fits, so that a line break falls
    // around it and not between its edge and the variable it starts with.
    if (child.type === "inlineCode")
      return [
        {
          type: "variableCode",
          data: {
            hName: "code",
            hProperties: { className: ["inline-block", "max-w-full"] },
          },
          children: parts,
        },
      ];
    if (child.type === "code") {
      const lang = child.lang ?? undefined;
      return [
        {
          type: "variableBlock",
          data: { hName: "pre" },
          children: [
            {
              type: "variableCode",
              data: {
                hName: "code",
                hProperties:
                  lang === undefined ? {} : { className: [`language-${lang}`] },
              },
              children: parts,
            },
          ],
        },
      ];
    }
    return [child];
  });
}

/**
 * Untrusted Markdown, rendered safely. cell may draw a table cell whose whole
 * text is plain: what it returns replaces the text, and null keeps it.
 * variables, when given, draws each template variable in place, in code too.
 */
export function Markdown({
  text,
  cell,
  variables,
}: {
  text: string;
  cell?: (text: string) => ReactNode | null;
  variables?: Variables | undefined;
}) {
  let all: Components =
    cell === undefined
      ? components
      : {
          ...components,
          td: ({ style, children }) => (
            <td style={style}>
              {(typeof children === "string" ? cell(children) : null) ??
                children}
            </td>
          ),
        };
  const plugins: NonNullable<Options["remarkPlugins"]> = [remarkGfm];
  if (variables !== undefined) {
    plugins.push(() => (tree: MdNode) => {
      splitVariables(tree, variables.pattern);
    });
    all = {
      ...all,
      var: ({ children }) =>
        variables.draw(typeof children === "string" ? children : ""),
    };
  }
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={plugins} components={all}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
