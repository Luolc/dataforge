// A Claude Code stream-json trace (`claude -p --output-format stream-json`),
// parsed into what the session view draws. The trace is untrusted: every
// field is checked for its type, and a line that is not a JSON object is kept
// as text where it stood.

/** One tool call, with its result once one arrived. */
export interface ToolCall {
  kind: "tool";
  id: string;
  name: string;
  input: unknown;
  /** The result's text; null when no result came back. */
  output: string | null;
  failed: boolean;
  /** Milliseconds from the trace's first time. */
  at: number | null;
  /** A subagent's messages, when this call started one. */
  children: Message[];
}

export type Block =
  | { kind: "text"; text: string }
  | { kind: "thinking"; text: string }
  | ToolCall;

/** One message: every block one assistant message ID, or one user turn, holds. */
export interface Message {
  kind: "message";
  role: "assistant" | "user";
  at: number | null;
  blocks: Block[];
}

/** A line that is not a JSON object, shown as it is. */
export interface BadLine {
  kind: "bad";
  line: number;
  text: string;
}

export interface Totals {
  seconds: number | null;
  costUsd: number | null;
  turns: number | null;
  /** Input tokens, cached ones counted, and output tokens. */
  tokensIn: number | null;
  tokensOut: number | null;
  model: string | null;
}

export interface Session {
  totals: Totals;
  entries: (Message | BadLine)[];
  tools: number;
  failed: number;
  thinking: number;
}

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const str = (value: unknown): string | null =>
  typeof value === "string" ? value : null;
const num = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const list = (value: unknown): unknown[] =>
  Array.isArray(value) ? (value as unknown[]) : [];

/** A tool result's content: a string, or blocks of which text is kept. */
function resultText(content: unknown): string {
  if (typeof content === "string") return content;
  return list(content)
    .map((block) => {
      if (!isObject(block)) return "";
      return block.type === "text"
        ? (str(block.text) ?? "")
        : `[${String(block.type)}]`;
    })
    .join("\n");
}

function blockOf(raw: unknown, at: number | null): Block | null {
  if (!isObject(raw)) return null;
  switch (raw.type) {
    case "text":
      return { kind: "text", text: str(raw.text) ?? "" };
    case "thinking":
      return { kind: "thinking", text: str(raw.thinking) ?? "" };
    case "tool_use":
      return {
        kind: "tool",
        id: str(raw.id) ?? "",
        name: str(raw.name) ?? "tool",
        input: raw.input,
        output: null,
        failed: false,
        at,
        children: [],
      };
    default:
      return null;
  }
}

/** Parse a stream-json trace's text. */
export function parseTrace(text: string): Session {
  const entries: (Message | BadLine)[] = [];
  const calls = new Map<string, ToolCall>();
  // The open message of each thread: the top level ("") or a subagent's call.
  const open = new Map<string, { id: string; message: Message }>();
  let first: number | null = null;
  let init: Json | null = null;
  let result: Json | null = null;

  for (const [index, line] of text.split("\n").entries()) {
    if (line.trim() === "") continue;
    let event: unknown;
    try {
      event = JSON.parse(line);
    } catch {
      event = undefined;
    }
    if (!isObject(event)) {
      entries.push({ kind: "bad", line: index + 1, text: line });
      continue;
    }
    const time = Date.parse(str(event.timestamp) ?? "");
    if (!Number.isNaN(time) && first === null) first = time;
    const at = Number.isNaN(time) || first === null ? null : time - first;
    if (event.type === "system" && event.subtype === "init") init = event;
    if (event.type === "result") result = event;
    if (event.type !== "assistant" && event.type !== "user") continue;

    const message = isObject(event.message) ? event.message : {};
    const parentId = str(event.parent_tool_use_id) ?? "";
    const parent = parentId === "" ? null : (calls.get(parentId) ?? null);
    const thread = parent === null ? "" : parentId;
    const into = parent === null ? entries : parent.children;
    const role = event.type;
    const content =
      typeof message.content === "string"
        ? [{ type: "text", text: message.content }]
        : list(message.content);

    // A user turn's tool results go to their calls; only its text is shown.
    const raws =
      role === "user"
        ? content.filter((raw) => {
            if (!isObject(raw) || raw.type !== "tool_result") return true;
            const call = calls.get(str(raw.tool_use_id) ?? "");
            if (call !== undefined) {
              call.output = resultText(raw.content);
              call.failed = raw.is_error === true;
            }
            return false;
          })
        : content;
    const blocks = raws
      .map((raw) => blockOf(raw, at))
      .filter((block): block is Block => block !== null);
    if (blocks.length === 0) continue;

    // stream-json writes each block of an assistant message as its own event,
    // all with the message's ID.
    const id = role === "assistant" ? (str(message.id) ?? "") : "";
    const last = open.get(thread);
    let target: Message;
    if (id !== "" && last?.id === id && into.at(-1) === last.message) {
      target = last.message;
    } else {
      target = { kind: "message", role, at, blocks: [] };
      into.push(target);
      open.set(thread, { id, message: target });
    }
    for (const block of blocks) {
      target.blocks.push(block);
      if (block.kind === "tool") calls.set(block.id, block);
    }
  }

  const all = [...calls.values()];
  const usage = isObject(result?.usage) ? result.usage : null;
  const tokensIn =
    usage === null
      ? null
      : (num(usage.input_tokens) ?? 0) +
        (num(usage.cache_read_input_tokens) ?? 0) +
        (num(usage.cache_creation_input_tokens) ?? 0);
  const ms = num(result?.duration_ms);
  return {
    totals: {
      seconds: ms === null ? null : ms / 1000,
      costUsd: num(result?.total_cost_usd),
      turns: num(result?.num_turns),
      tokensIn,
      tokensOut: usage === null ? null : num(usage.output_tokens),
      model: str(init?.model),
    },
    entries,
    tools: all.length,
    failed: all.filter((call) => call.failed).length,
    thinking: countThinking(entries),
  };
}

function countThinking(entries: (Message | BadLine)[]): number {
  let count = 0;
  for (const entry of entries) {
    if (entry.kind !== "message") continue;
    for (const block of entry.blocks) {
      if (block.kind === "thinking") count++;
      if (block.kind === "tool") count += countThinking(block.children);
    }
  }
  return count;
}
