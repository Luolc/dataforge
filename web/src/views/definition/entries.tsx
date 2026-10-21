// The kinds of definition entry (text and rubric, and one of another kind),
// the version line over them, and what shows when an attempt has no snapshot
// or one not of the snapshot's shape.
import { ChevronDown, ChevronRight, CircleDashed } from "lucide-react";
import { Tooltip } from "radix-ui";
import { Fragment, type ReactNode, useId, useState } from "react";
import { Link } from "react-router";

import { Note } from "../../ui/card";
import { Highlighted } from "../../ui/code";
import { fileTypeOf } from "../../ui/file-kind";
import { languageOf } from "../../ui/language";
import { Markdown } from "../../ui/markdown";
import { NOT_RUN } from "../../ui/status";
import { ToneLabel } from "../../ui/status-mark";
import { attemptHref } from "../attempt/href";
import {
  type Criterion,
  type Definition,
  type Entry,
  isRubric,
  isText,
  type ReadDefinition,
  type RubricEntry,
  type TextEntry,
} from "./types";
import { Placeholder } from "./placeholder";
import { DIRTY } from "./words";

/** How many lines of a text entry show before Show all. */
const FOLDED_LINES = 16;

/** How many characters of a version value show; the rest in a tooltip. */
const SHORT = 12;

const fold =
  "target inline-flex items-center gap-1.5 rounded-row px-1.5 text-xs font-medium text-fg-2 hover:bg-hover hover:text-fg";

const variableMark =
  "rounded-code bg-code-gutter px-0.5 font-medium text-syn-variable ring-1 ring-line-subtle";

/** A file name of the format, an extension, for the file-kind table. */
const named = (format: string) => `x.${format}`;

/** A version value cut to SHORT characters, its whole in a tooltip (L46). */
function Short({ value }: { value: string | null }) {
  if (value === null) return <>—</>;
  if (value.length <= SHORT) return <>{value}</>;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        {/* eslint-disable-next-line jsx-a11y-x/no-noninteractive-tabindex -- the tooltip opens on focus, so a keyboard can reach it */}
        <span tabIndex={0} aria-label={value} className="rounded-row">
          {value.slice(0, SHORT)}
        </span>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="bottom"
          align="start"
          sideOffset={4}
          className="z-10 max-w-96 rounded-row border border-line bg-active px-2.5 py-1.5 font-mono text-xs break-all text-fg"
        >
          {value}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

/**
 * The snapshot's version: the commit it was made at, whether the tree had
 * changes, its prompt_version, and, on a project's page, the attempt it was
 * taken from.
 */
export function VersionLine({
  definition,
  project,
  from,
}: {
  definition: Definition;
  project: string;
  /** The attempt the snapshot is from, on a project's page; null on its own. */
  from: string | null;
}) {
  const v = definition.version;
  return (
    <Tooltip.Provider delayDuration={200}>
      <dl className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-3">
        {from !== null && (
          <div className="flex items-center gap-1.5">
            <dt>From attempt</dt>
            <dd>
              <Link
                to={attemptHref(project, from, {})}
                className="inline-flex target items-center font-mono link hover:underline"
              >
                {from}
              </Link>
            </dd>
          </div>
        )}
        <div className="flex items-center gap-1.5">
          <dt>commit</dt>
          <dd className="flex items-center gap-1.5 font-mono text-fg">
            <Short value={v.commit} />
            {v.dirty === true && <ToneLabel {...DIRTY} />}
          </dd>
        </div>
        <div className="flex items-baseline gap-1.5">
          <dt>prompt_version</dt>
          <dd className="font-mono text-fg">
            <Short value={v.prompt_version} />
          </dd>
        </div>
      </dl>
    </Tooltip.Provider>
  );
}

/** How a Markdown entry shows: rendered, or as the model reads it. */
type View = "preview" | "code";

/** An entry under its title: a text, a rubric, or a card of another kind. */
export function EntryCard({
  entry,
  filled,
}: {
  entry: Entry;
  /** Whether the variables show the values this attempt filled in. */
  filled: boolean;
}) {
  if (isText(entry)) return <TextCard entry={entry} filled={filled} />;
  if (isRubric(entry)) return <RubricCard entry={entry} />;
  return (
    <EntryFrame title={entry.title} aside={<span>{entry.kind}</span>}>
      <p className="px-5 py-3 text-fg-3">
        The viewer does not know entries of kind{" "}
        <code className="font-mono">{entry.kind}</code>.
      </p>
    </EntryFrame>
  );
}

function EntryFrame({
  title,
  aside,
  children,
}: {
  title: string;
  aside: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="@container overflow-hidden rounded-panel border border-line bg-card"
    >
      <header className="flex items-center justify-between gap-3 border-b border-line bg-head px-5 py-2.5">
        <h3 className="font-mono text-md font-medium">{title}</h3>
        <div className="flex items-center gap-3 text-xs text-fg-3">{aside}</div>
      </header>
      {children}
    </section>
  );
}

const templateWord = { dollar: "${…} template", jinja: "Jinja template" };

function TextCard({ entry, filled }: { entry: TextEntry; filled: boolean }) {
  const markdown = fileTypeOf(named(entry.format)).kind === "markdown";
  const [view, setView] = useState<View>(markdown ? "preview" : "code");
  const count = entry.variables.length;
  const words = [
    entry.template !== null ? templateWord[entry.template] : entry.format,
    entry.template !== null
      ? `${count.toString()} variable${count === 1 ? "" : "s"}`
      : null,
    entry.note ?? null,
  ]
    .filter((s) => s !== null)
    .join(" · ");
  const aside = (
    <>
      <span>{words}</span>
      {markdown && <ViewSwitch view={view} onChange={setView} />}
    </>
  );
  const template = entry.template;
  return (
    <EntryFrame title={entry.title} aside={aside}>
      {view === "preview" ? (
        <>
          <div className="px-6 py-4">
            <Markdown
              text={entry.text}
              variables={
                template === null
                  ? undefined
                  : {
                      pattern: patterns[template],
                      draw: (name) => (
                        <Placeholder
                          name={name}
                          spelled={spelled(template, name)}
                          variable={entry.variables.find(
                            (v) => v.name === name,
                          )}
                          filled={filled}
                        />
                      ),
                    }
              }
            />
          </div>
          {count > 0 && <FoldedVariables entry={entry} />}
        </>
      ) : (
        <div
          className={
            count > 0
              ? "grid @3xl:grid-cols-[minmax(0,1fr)_var(--spacing-variables)]"
              : undefined
          }
        >
          <TemplateText entry={entry} />
          {count > 0 && <Variables entry={entry} beside />}
        </div>
      )}
    </EntryFrame>
  );
}

const views: { key: View; label: string }[] = [
  { key: "preview", label: "Preview" },
  { key: "code", label: "Code" },
];

/** Preview or Code, as on a forge's file page. */
function ViewSwitch({
  view,
  onChange,
}: {
  view: View;
  onChange: (view: View) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Show as"
      className="flex gap-0.5 rounded-panel border border-chip-line bg-chip p-0.5"
    >
      {views.map((v) => (
        <button
          key={v.key}
          type="button"
          aria-pressed={view === v.key}
          onClick={() => {
            onChange(v.key);
          }}
          className="inline-flex target items-center rounded-row px-2.5 font-medium text-fg-2 hover:bg-hover hover:text-fg aria-pressed:bg-chip-on aria-pressed:text-chip-on-fg"
        >
          {v.label}
        </button>
      ))}
    </div>
  );
}

/** The variables' table under rendered text, folded at first. */
function FoldedVariables({ entry }: { entry: TextEntry }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <div className="border-t border-line-subtle">
      <div className="px-3 py-1">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => {
            setOpen((o) => !o);
          }}
          className={fold}
        >
          {open ? (
            <ChevronDown className="size-3.5 text-icon" aria-hidden />
          ) : (
            <ChevronRight className="size-3.5 text-icon" aria-hidden />
          )}
          {`Variables · ${entry.variables.length.toString()}`}
        </button>
      </div>
      <div id={id} hidden={!open}>
        <Variables entry={entry} beside={false} />
      </div>
    </div>
  );
}

/** The text with its variables marked, folded past FOLDED_LINES. */
function TemplateText({ entry }: { entry: TextEntry }) {
  const id = useId();
  const [all, setAll] = useState(false);
  const lines = entry.text.replace(/\n$/, "").split("\n");
  const long = lines.length > FOLDED_LINES;
  const shown = all || !long ? lines : lines.slice(0, FOLDED_LINES);
  return (
    <div className="min-w-0">
      <div
        id={id}
        className="grid grid-cols-[auto_minmax(0,1fr)] bg-code-bg py-2.5 font-mono text-code tracking-normal"
      >
        {shown.map((line, i) => (
          // A line's number is what it is: the text is read once.
          // eslint-disable-next-line @eslint-react/no-array-index-key
          <Fragment key={i}>
            <span
              aria-hidden
              className="bg-code-gutter pr-3 pl-4 text-right text-faint select-none"
            >
              {i + 1}
            </span>
            <code className="pr-5 pl-3 break-words whitespace-pre-wrap">
              {entry.template === null ? (
                <Highlighted
                  text={line}
                  language={languageOf(named(entry.format))}
                />
              ) : (
                <Marked text={line} template={entry.template} />
              )}
              {line === "" && "\n"}
            </code>
          </Fragment>
        ))}
      </div>
      {long && (
        <div className="border-t border-line-subtle px-3 py-1">
          <button
            type="button"
            aria-expanded={all}
            aria-controls={id}
            onClick={() => {
              setAll((a) => !a);
            }}
            className={fold}
          >
            {all ? (
              <ChevronDown className="size-3.5 text-icon" aria-hidden />
            ) : (
              <ChevronRight className="size-3.5 text-icon" aria-hidden />
            )}
            {all
              ? "Show the first lines only"
              : `Show all ${lines.length.toString()} lines`}
          </button>
        </div>
      )}
    </div>
  );
}

const patterns = {
  // After an even number of $, since $$ is a $ as string.Template reads it.
  dollar:
    /(?<=(?:^|[^$])(?:\$\$)*)\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)/g,
  jinja: /\{\{\s*[A-Za-z_][A-Za-z0-9_.]*\s*\}\}/g,
};

/** A template's text with each variable marked. */
function Marked({
  text,
  template,
}: {
  text: string;
  template: "dollar" | "jinja";
}) {
  const parts: ReactNode[] = [];
  let at = 0;
  for (const m of text.matchAll(patterns[template])) {
    parts.push(text.slice(at, m.index));
    parts.push(
      <mark key={m.index} className={variableMark}>
        {m[0]}
      </mark>,
    );
    at = m.index + m[0].length;
  }
  parts.push(text.slice(at));
  return <>{parts}</>;
}

const spelled = (template: TextEntry["template"], name: string) =>
  template === "jinja" ? `{{ ${name} }}` : `\${${name}}`;

/** Each variable of a template and where its value comes from. */
function Variables({ entry, beside }: { entry: TextEntry; beside: boolean }) {
  return (
    <div
      className={
        beside ? "border-t border-line @3xl:border-t-0 @3xl:border-l" : ""
      }
    >
      <table className="w-full border-collapse text-sm">
        <caption
          className={
            beside
              ? "px-4 pt-2.5 pb-1.5 text-left text-xs font-medium text-fg-3"
              : "sr-only"
          }
        >
          Variables
        </caption>
        <thead>
          <tr className="border-y border-line bg-head text-xs text-fg-2">
            <th className="px-4 py-1 text-left font-medium">Variable</th>
            <th className="px-2 py-1 text-left font-medium">From</th>
          </tr>
        </thead>
        <tbody>
          {entry.variables.map((v) => (
            <tr key={v.name} className="border-b border-line-subtle">
              <td className="px-4 py-1.5 align-top whitespace-nowrap">
                <code className={`font-mono text-xs ${variableMark}`}>
                  {spelled(entry.template, v.name)}
                </code>
              </td>
              <td className="px-2 py-1.5 align-top text-xs text-fg-2">
                {v.source}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RubricCard({ entry }: { entry: RubricEntry }) {
  const criteria = entry.rubric.criteria;
  const aside = [
    `${criteria.length.toString()} ${criteria.length === 1 ? "criterion" : "criteria"}`,
    ...(entry.note === undefined ? [] : [entry.note]),
  ].join(" · ");
  return (
    <EntryFrame title={entry.title} aside={aside}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-1.5">
        <System text={entry.rubric.system} />
        {entry.reads !== undefined && (
          <p className="text-xs text-fg-3">
            Reads{" "}
            {entry.reads.map((r, i) => (
              <Fragment key={r}>
                {i > 0 && ", "}
                <code className="font-mono text-fg-2">{r}</code>
              </Fragment>
            ))}
          </p>
        )}
      </div>
      <Criteria criteria={criteria} />
    </EntryFrame>
  );
}

/** The rubric's system text, folded at first. */
function System({ text }: { text: string }) {
  const id = useId();
  const [shown, setShown] = useState(false);
  const lines = text.replace(/\n$/, "").split("\n").length;
  return (
    <div className="contents">
      <button
        type="button"
        aria-expanded={shown}
        aria-controls={id}
        onClick={() => {
          setShown((s) => !s);
        }}
        className={fold}
      >
        {shown ? (
          <ChevronDown className="size-3.5 text-icon" aria-hidden />
        ) : (
          <ChevronRight className="size-3.5 text-icon" aria-hidden />
        )}
        System text
        <span className="font-normal text-fg-3">
          {lines} line{lines === 1 ? "" : "s"}
        </span>
      </button>
      <pre
        id={id}
        hidden={!shown}
        className="order-last w-full rounded-row border border-line-subtle bg-code-bg px-3 py-2 font-mono text-code whitespace-pre-wrap"
      >
        {text}
      </pre>
    </div>
  );
}

/** One row per criterion; a row opens to its check, pass_if and fail_if. */
function Criteria({ criteria }: { criteria: Criterion[] }) {
  const baseId = useId();
  const [open, setOpen] = useState(() => new Set<string>());
  const toggle = (id: string) => {
    setOpen((was) => {
      const next = new Set(was);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  return (
    <table className="w-full table-fixed border-collapse text-sm">
      <colgroup>
        <col className="w-10" />
        <col className="w-16" />
        <col />
      </colgroup>
      <thead>
        <tr className="border-y border-line bg-head text-xs text-fg-2">
          <th className="py-1">
            <span className="sr-only">Open</span>
          </th>
          <th className="px-2 py-1 text-left font-medium">ID</th>
          <th className="px-2 py-1 text-left font-medium">Question</th>
        </tr>
      </thead>
      <tbody>
        {criteria.map((c) => {
          const isOpen = open.has(c.id);
          const detail = `${baseId}-${c.id}`;
          return (
            <Fragment key={c.id}>
              <tr
                className={`border-b border-line-subtle hover:bg-hover ${isOpen ? "bg-active" : ""}`}
                onClick={() => {
                  toggle(c.id);
                }}
              >
                <td className="py-1 pl-2 align-top">
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    aria-controls={detail}
                    aria-label={`${c.id}: check, pass if and fail if`}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggle(c.id);
                    }}
                    className="grid target place-items-center rounded-row hover:bg-hover"
                  >
                    {isOpen ? (
                      <ChevronDown className="size-3.5 text-icon" aria-hidden />
                    ) : (
                      <ChevronRight
                        className="size-3.5 text-icon"
                        aria-hidden
                      />
                    )}
                  </button>
                </td>
                <td className="px-2 py-1.5 align-top font-mono text-xs">
                  {c.id}
                </td>
                <td
                  className={`px-2 py-1.5 pr-5 align-top ${isOpen ? "" : "truncate"}`}
                >
                  {c.question}
                </td>
              </tr>
              <tr id={detail} hidden={!isOpen} className="bg-active">
                <td colSpan={2} />
                <td className="px-2 pt-0.5 pr-5 pb-3">
                  <dl className="flex flex-col gap-2">
                    {[
                      ["check", c.check],
                      ["pass_if", c.pass_if],
                      ["fail_if", c.fail_if],
                    ].map(([field, text]) => (
                      <div key={field}>
                        <dt className="font-mono text-2xs text-fg-3">
                          {field}
                        </dt>
                        <dd className={text === undefined ? "text-fg-3" : ""}>
                          {text ?? "Not given."}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </td>
              </tr>
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

/** An attempt with no snapshot: not run (L40), not a failure. */
export function NotRecorded({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 rounded-panel border border-dashed border-line px-5 py-4">
      <CircleDashed
        className="mt-0.5 size-icon-md flex-none text-icon"
        aria-hidden
      />
      <div>
        <p className="font-medium">{NOT_RUN}</p>
        <p className="text-fg-2">{children}</p>
      </div>
    </div>
  );
}

/** A snapshot file not of the snapshot's shape: why, then the file as text. */
export function Unshaped({
  read,
}: {
  read: Extract<ReadDefinition, { shape: "other" }>;
}) {
  return (
    <div className="flex flex-col gap-3">
      <Note>
        This definition is not in a shape the viewer knows ({read.why}).
      </Note>
      <pre className="overflow-x-auto rounded-code bg-code-bg px-4 py-3 font-mono text-code whitespace-pre-wrap">
        {read.text}
      </pre>
    </div>
  );
}
