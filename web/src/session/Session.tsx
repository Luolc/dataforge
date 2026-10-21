// A parsed session drawn as one column: a sticky header with the totals, then
// one row per message with the time since the start in a narrow gutter. Every
// text in it is from the trace, untrusted: Markdown goes through the shared
// renderer, everything else is shown as text.
import {
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
} from "lucide-react";
import { createContext, type ReactNode, use, useId, useState } from "react";

import type { Project } from "../api/types";
import { duration, usd } from "../ui/format";
import { Markdown } from "../ui/markdown";
import { toneGlyph, toneText } from "../ui/status";
import type { BadLine, Block, Message, Session, ToolCall } from "./parse";

export type ThinkingMode = Project["session"]["thinking"];

/** The totals a session's header shows: all of them, or the model alone. */
export type SessionTotals = "all" | "model";

/** More tool calls than this in one message fold into one line. */
const FOLD_OVER = 4;

/**
 * What the header's Expand all and Collapse all say to the folds below: the
 * last one pressed (`open`, counted by `serial`, 0 before any) and whether
 * every fold starts open.
 */
const FoldContext = createContext({
  serial: 0,
  open: false,
  startOpen: false,
});

/** Whether the gutter shows each message's time since the start. */
const TimesContext = createContext(true);

const count = new Intl.NumberFormat("en-US", { notation: "compact" });

export function SessionBody({
  session,
  thinking,
  badge,
  startOpen = false,
  totals: shownTotals = "all",
}: {
  session: Session;
  thinking: ThinkingMode;
  badge?: ReactNode;
  /** Whether every fold starts open; the buttons still change it after. */
  startOpen?: boolean;
  /**
   * Which totals the header shows: all of them, or the model alone, and then
   * no message's time in the gutter either.
   */
  totals?: SessionTotals;
}) {
  const [fold, setFold] = useState({ serial: 0, open: startOpen, startOpen });
  const { totals } = session;
  // How thinking shows; null when the project hides all of it.
  const shownThinking = thinking === "hidden" ? null : thinking;
  return (
    <FoldContext value={fold}>
      <section className="flex flex-col rounded-panel border border-line bg-panel">
        <header className="sticky top-0 z-10 flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-t-panel border-b border-line bg-panel px-5 py-3 text-xs text-fg-2">
          {badge}
          {shownTotals === "all" && (
            <>
              <Total label="duration">
                {totals.seconds === null ? "—" : duration(totals.seconds)}
              </Total>
              <Total label="cost">
                {totals.costUsd === null ? "—" : usd(totals.costUsd)}
              </Total>
              <Total label="turns">{totals.turns ?? "—"}</Total>
              <Total label="tokens">
                {totals.tokensIn === null ? "—" : count.format(totals.tokensIn)}{" "}
                in ·{" "}
                {totals.tokensOut === null
                  ? "—"
                  : count.format(totals.tokensOut)}{" "}
                out
              </Total>
            </>
          )}
          <Total label="model">
            <span className="font-mono">{totals.model ?? "—"}</span>
          </Total>
          {shownTotals === "all" && (
            <span>
              {session.tools} tool calls
              {session.failed > 0 && (
                <span className={toneText.bad}> · {session.failed} failed</span>
              )}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <FoldButton
              label="Expand all"
              Icon={ChevronsUpDown}
              onClick={() => {
                setFold({ serial: fold.serial + 1, open: true, startOpen });
              }}
            />
            <FoldButton
              label="Collapse all"
              Icon={ChevronsDownUp}
              onClick={() => {
                setFold({ serial: fold.serial + 1, open: false, startOpen });
              }}
            />
          </div>
        </header>
        <TimesContext value={shownTotals === "all"}>
          <ol className="flex flex-col py-2">
            {session.preamble.length > 0 && (
              <Preamble lines={session.preamble} />
            )}
            {session.entries.map((entry, i) =>
              entry.kind === "bad" ? (
                <BadRow key={i} entry={entry} />
              ) : (
                <MessageRow key={i} message={entry} thinking={shownThinking} />
              ),
            )}
          </ol>
        </TimesContext>
      </section>
    </FoldContext>
  );
}

function FoldButton({
  label,
  Icon,
  onClick,
}: {
  label: string;
  Icon: typeof ChevronsUpDown;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex target items-center gap-1.5 rounded-row border border-line px-2.5 font-medium hover:bg-hover"
    >
      <Icon className="size-3.5 flex-none text-icon" aria-hidden />
      {label}
    </button>
  );
}

function Total({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="tabular-nums">
      <span className="text-fg-3">{label} </span>
      <span className="text-fg">{children}</span>
    </span>
  );
}

function Row({ at, children }: { at: number | null; children: ReactNode }) {
  const times = use(TimesContext);
  return (
    <li className="grid grid-cols-[calc(var(--spacing)*16)_1fr] gap-3 px-5 py-1.5">
      <span className="pt-0.5 text-right font-mono text-2xs text-fg-3 tabular-nums">
        {at === null || !times ? "" : duration(at / 1000)}
      </span>
      <div className="flex min-w-0 flex-col gap-1.5">{children}</div>
    </li>
  );
}

/**
 * The lines before the session began, folded into one grey line that opens
 * to show them as they are.
 */
function Preamble({ lines }: { lines: BadLine[] }) {
  const { open, id, button } = useToggle(false);
  const more = lines.length - 1;
  return (
    <Row at={null}>
      <button
        type="button"
        {...button}
        className="flex target min-w-0 items-center gap-1.5 text-left text-xs text-fg-3 hover:text-fg-2"
      >
        <Chevron open={open} />
        <span className="min-w-0 truncate">{lines[0]?.text}</span>
        {more > 0 && (
          <span className="flex-none">
            {` · ${more.toString()} more ${more === 1 ? "line" : "lines"}`}
          </span>
        )}
      </button>
      <pre
        id={id}
        hidden={!open}
        className="overflow-x-auto rounded-code bg-code-bg px-3 py-2 font-mono text-code whitespace-pre-wrap text-fg-2"
      >
        {lines.map((line) => line.text).join("\n")}
      </pre>
    </Row>
  );
}

function BadRow({ entry }: { entry: BadLine }) {
  return (
    <Row at={null}>
      <p className="text-xs text-fg-3">
        Line {entry.line} is not JSON, shown as it is:
      </p>
      <pre className="overflow-x-auto rounded-code bg-code-bg px-3 py-2 font-mono text-code whitespace-pre-wrap">
        {entry.text}
      </pre>
    </Row>
  );
}

/** A message's blocks, its tool calls folded into one line past FOLD_OVER. */
function MessageRow({
  message,
  thinking,
}: {
  message: Message;
  thinking: ThinkingMode | null;
}) {
  const tools = message.blocks.filter((b): b is ToolCall => b.kind === "tool");
  const fold = tools.length > FOLD_OVER;
  const shown: ReactNode[] = [];
  message.blocks.forEach((block, i) => {
    if (
      block.kind === "thinking" &&
      (thinking === null || block.text.trim() === "")
    )
      return;
    if (block.kind === "tool" && fold) {
      if (block === tools[0])
        shown.push(<ToolGroup key={i} calls={tools} thinking={thinking} />);
      return;
    }
    shown.push(
      <BlockView
        key={i}
        block={block}
        role={message.role}
        thinking={thinking}
      />,
    );
  });
  if (shown.length === 0) return null;
  return <Row at={message.at}>{shown}</Row>;
}

function BlockView({
  block,
  role,
  thinking,
}: {
  block: Block;
  role: Message["role"];
  thinking: ThinkingMode | null;
}) {
  switch (block.kind) {
    case "text":
      return role === "user" ? (
        <div className="rounded-row border border-line px-3 py-2">
          <p className="mb-1 text-xs text-fg-3">Prompt</p>
          <Markdown text={block.text} />
        </div>
      ) : (
        <Markdown text={block.text} />
      );
    case "thinking":
      return <Thinking text={block.text} open={thinking === "expanded"} />;
    case "tool":
      return <ToolRow call={block} thinking={thinking} />;
  }
}

/**
 * A toggle's button and the part it opens, tied by aria-controls. It follows
 * the header's Expand all and Collapse all: a press after it was last
 * rendered sets it, and one that came before it was mounted sets where it
 * starts.
 */
function useToggle(initial: boolean) {
  const fold = use(FoldContext);
  const [open, setOpen] = useState(
    fold.serial > 0 ? fold.open : fold.startOpen || initial,
  );
  const [serial, setSerial] = useState(fold.serial);
  if (serial !== fold.serial) {
    setSerial(fold.serial);
    setOpen(fold.open);
  }
  const id = useId();
  const button = {
    "aria-expanded": open,
    "aria-controls": id,
    onClick: () => {
      setOpen(!open);
    },
  };
  return { open, id, button };
}

function Chevron({ open }: { open: boolean }) {
  const Icon = open ? ChevronDown : ChevronRight;
  return <Icon className="size-3.5 flex-none text-icon" aria-hidden />;
}

function Thinking({ text, open: initial }: { text: string; open: boolean }) {
  const { open, id, button } = useToggle(initial);
  const words = text.trim().split(/\s+/).length;
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        {...button}
        className="flex target max-w-full min-w-0 items-center gap-1.5 self-start rounded-row text-left text-xs text-fg-2 hover:bg-hover"
      >
        <Chevron open={open} />
        <span className="flex-none font-medium">Thinking · {words} words</span>
        {!open && (
          <span className="truncate text-fg-3">{text.split("\n")[0]}</span>
        )}
      </button>
      <p
        id={id}
        hidden={!open}
        className="pl-5 text-xs whitespace-pre-wrap text-fg-2"
      >
        {text}
      </p>
    </div>
  );
}

function stringField(input: unknown, key: string): string | null {
  if (typeof input !== "object" || input === null) return null;
  const value: unknown = Reflect.get(input, key);
  return typeof value === "string" ? value : null;
}

const KEY_FIELDS = [
  "command",
  "file_path",
  "pattern",
  "path",
  "description",
  "url",
];

/** The part of a call's input that names what it did. */
function keyArgument(input: unknown): string {
  for (const key of KEY_FIELDS) {
    const value = stringField(input, key);
    if (value !== null) return value;
  }
  return JSON.stringify(input);
}

function inputText(input: unknown): string {
  return stringField(input, "command") ?? JSON.stringify(input, null, 2);
}

function Failed() {
  const Glyph = toneGlyph.bad;
  return (
    <span className={`flex flex-none items-center gap-1 ${toneText.bad}`}>
      <Glyph className="size-3" aria-hidden />
      failed
    </span>
  );
}

function ToolRow({
  call,
  thinking,
}: {
  call: ToolCall;
  thinking: ThinkingMode | null;
}) {
  const { open, id, button } = useToggle(call.failed);
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        {...button}
        className="flex target min-w-0 items-center gap-2 rounded-row px-1 text-left font-mono text-xs hover:bg-hover"
      >
        <Chevron open={open} />
        <span className="flex-none font-medium text-fg">{call.name}</span>
        <span className="min-w-0 flex-1 truncate text-fg-2">
          {keyArgument(call.input)}
        </span>
        {call.children.length > 0 && (
          <span className="flex-none text-fg-3">subagent</span>
        )}
        {call.failed && <Failed />}
        {call.output === null && (
          <span className="flex-none text-fg-3">no result</span>
        )}
      </button>
      <div id={id} hidden={!open} className="flex flex-col gap-2 pl-5">
        <Output title="Input" text={inputText(call.input)} />
        <Output
          title={call.failed ? "Output · open because it failed" : "Output"}
          text={call.output ?? "No result came back."}
          bad={call.failed}
        />
      </div>
      {call.children.length > 0 && (
        <ol
          aria-label={`Subagent of ${call.name}`}
          className="ml-2 flex flex-col border-l border-line"
        >
          {call.children.map((child, i) => (
            <MessageRow key={i} message={child} thinking={thinking} />
          ))}
        </ol>
      )}
    </div>
  );
}

function Output({
  title,
  text,
  bad = false,
}: {
  title: string;
  text: string;
  bad?: boolean;
}) {
  return (
    <div
      className={`overflow-hidden rounded-row border ${bad ? "border-bad" : "border-line"}`}
    >
      <p
        className={`border-b px-3 py-1 font-mono text-2xs ${bad ? `border-bad bg-bad-bg ${toneText.bad}` : "border-line text-fg-3"}`}
      >
        {title}
      </p>
      <pre className="max-h-96 overflow-auto bg-code-bg px-3 py-2 font-mono text-code whitespace-pre-wrap">
        {text}
      </pre>
    </div>
  );
}

/** More than FOLD_OVER calls of one message, as one line that opens to them. */
function ToolGroup({
  calls,
  thinking,
}: {
  calls: ToolCall[];
  thinking: ThinkingMode | null;
}) {
  const failed = calls.filter((call) => call.failed).length;
  const { open, id, button } = useToggle(failed > 0);
  const names = new Map<string, number>();
  for (const call of calls)
    names.set(call.name, (names.get(call.name) ?? 0) + 1);
  const byName = [...names].map(([name, n]) => `${name} ×${n}`).join(", ");
  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        {...button}
        className="flex target min-w-0 items-center gap-2 rounded-row px-1 text-left text-xs hover:bg-hover"
      >
        <Chevron open={open} />
        <span className="flex-none font-medium">{calls.length} tool calls</span>
        <span className="min-w-0 flex-1 truncate font-mono text-fg-3">
          {byName}
        </span>
        <span className={failed > 0 ? toneText.bad : "text-fg-3"}>
          {failed} of {calls.length} failed
        </span>
      </button>
      <div id={id} hidden={!open} className="flex flex-col gap-1 pl-5">
        {calls.map((call, i) => (
          <ToolRow key={i} call={call} thinking={thinking} />
        ))}
      </div>
    </div>
  );
}
