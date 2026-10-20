// Markdown from an attempt, which agents and the code under test wrote: it is
// untrusted. Raw HTML shows as text, an image is a link that loads nothing,
// and a link opens only on a click, in a new tab with no opener or referrer.
// react-markdown turns raw HTML into text unless a plugin such as rehype-raw
// is added; none may be.
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { fenceLanguage, Highlighted } from "./code";

const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  img: ({ src, alt }) => (
    <a
      href={typeof src === "string" ? src : undefined}
      target="_blank"
      rel="noopener noreferrer"
    >
      {`image: ${alt !== undefined && alt !== "" ? alt : typeof src === "string" ? src : ""}`}
    </a>
  ),
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

/** Untrusted Markdown, rendered safely. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
