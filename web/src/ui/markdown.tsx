// Markdown from an attempt, which agents and the code under test wrote: it is
// untrusted. Raw HTML shows as text, an image is a link that loads nothing,
// and a link opens only on a click, in a new tab with no opener or referrer,
// but a link to #<id> moves to that heading or footnote of the same text.
// react-markdown turns raw HTML into text unless a plugin such as rehype-raw
// is added; none may be.
import type { MouseEvent, ReactNode } from "react";
import ReactMarkdown, { type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";

import { Highlighted } from "./code";
import { fenceLanguage } from "./language";

// A heading's id starts with what remark-gfm's footnotes start theirs with,
// so that no id of the text is one the page's own code looks up.
const PREFIX = "user-content-";

/**
 * Move to the element of the link's own text whose id the link's #<id>
 * names, with or without PREFIX, and focus it; a name the text does not hold
 * moves nothing.
 */
function moveTo(event: MouseEvent<HTMLAnchorElement>, href: string) {
  event.preventDefault();
  let name;
  try {
    name = decodeURIComponent(href.slice(1));
  } catch {
    return;
  }
  const ids = [`${PREFIX}${name}`, name];
  const text = event.currentTarget.closest(".md");
  const all = [...(text?.querySelectorAll<HTMLElement>("[id]") ?? [])];
  const target = ids
    .map((id) => all.find((e) => e.id === id))
    .find((e) => e !== undefined);
  if (target === undefined) return;
  target.scrollIntoView();
  target.focus({ preventScroll: true });
}

const components: Components = {
  a: ({ href, children }) =>
    href?.startsWith("#") === true ? (
      <a
        href={href}
        onClick={(event) => {
          moveTo(event, href);
        }}
      >
        {children}
      </a>
    ) : (
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

const textOf = (node: MdNode): string =>
  node.value ?? (node.children ?? []).map(textOf).join("");

/**
 * A heading's anchor as GitHub makes it from the heading's text: lower case,
 * with no punctuation or symbol, and a hyphen for each space.
 */
function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, "")
    .replace(/ /g, "-");
}

// Gives each heading the id of its slug after PREFIX, a slug the text has
// already taken followed by -1, -2 and so on, as GitHub numbers them; a
// heading can be focused, so that a link to it moves the focus there too.
function headingIds(tree: MdNode) {
  const taken = new Set<string>();
  const visit = (node: MdNode) => {
    if (node.type === "heading") {
      const base = slug(textOf(node));
      let id = base;
      for (let n = 1; taken.has(id); n++) id = `${base}-${n.toString()}`;
      taken.add(id);
      node.data = {
        ...node.data,
        hProperties: {
          ...node.data?.hProperties,
          id: PREFIX + id,
          tabIndex: -1,
        },
      };
    }
    node.children?.forEach(visit);
  };
  visit(tree);
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
  const plugins: NonNullable<Options["remarkPlugins"]> = [
    remarkGfm,
    () => headingIds,
  ];
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
